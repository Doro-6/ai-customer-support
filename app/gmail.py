"""邮箱客户端：用 IMAP + SMTP 替代课程的 Gmail API 版本。

对外接口与课程 `app/gmail.py` 完全一致，所以 `pipeline.py`、`guards.py`、
`poller.py`、`evals/` 都**不需要修改**：

    Gmail()  .address  .label_ids
    .list_new_message_ids(max_results)  .get_message(id)  .get_thread(thread_id)
    .add_label(id, name)  .is_escalated(thread)  .send_reply(message, body)
    get_headers(message)  get_body(message)  has_attachments(message)
    strip_quoted(text)  format_thread(thread, own_address)

与 Gmail 版的差异（都是有意的）：

1. **label 用 IMAP 文件夹模拟**。`add_label` 是 COPY 一份到同名文件夹，原邮件
   留在 INBOX，所以语义与 Gmail 标签一致：邮件既在收件箱，也带标签。
2. **没有原生「会话」概念**。threadId 取 `References` 头里的第一个 Message-ID
   （会话根消息）；没有 References 时用自身的 Message-ID。
   会话重组靠 `SEARCH HEADER MESSAGE-ID` + `SEARCH HEADER REFERENCES`。
3. **send_reply 会把回复副本 APPEND 回 INBOX**（同时打上 ai-processed），
   这样 `format_thread` 能看到机器人自己之前说过什么，行为与 Gmail 线程一致。
   这份副本会被 `guards.skip_reason` 按发件人地址跳过——课程本来就是这么设计
   防自问自答的。
4. 正文解析、编码、引文剥离按国内邮箱的实际情况加固：支持 gb2312/gb18030，
   HTML-only 邮件会转成纯文本，识别「原始邮件」「在…写道」等引文标记。
"""

from __future__ import annotations

import email
import functools
import hashlib
import imaplib
import logging
import re
import smtplib
import ssl
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime
from html import unescape

from app.config import settings

# 163/126 要求在登录后先发 ID 命令，否则报 Unsafe Login；QQ 不需要，但发了无害。
imaplib.Commands.setdefault("ID", ("NONAUTH", "AUTH", "SELECTED"))

logger = logging.getLogger(__name__)

PROCESSED_LABEL = "ai-processed"
ESCALATED_LABEL = "needs-human"

# 引文分隔标记：命中即认为后面都是被引用的历史内容。
_QUOTE_TAGS = (
    "-----original message-----",
    "-----原始邮件-----",
    "----- 原始邮件 -----",
    "------------------ 原始邮件",
)

_HTML_TAG = re.compile(r"<[^>]+>")
_MESSAGE_ID = re.compile(rb"<[^<>@\s]+@[^<>\s]+>")
_FETCH_UID = re.compile(rb"UID (\d+)")
_FOLDER_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")

# 取头时统一用这一组字段：Message-ID 用于会话，Date+Subject+From 用于缺 ID 时的身份回退。
_HEADER_FIELDS = "MESSAGE-ID REFERENCES DATE SUBJECT FROM TO"


def _identity(headers: dict[str, str]) -> str:
    """给一封邮件算稳定身份，用来跨文件夹判断「这封是否已打过标签」。

    大多数邮件有 Message-ID，直接用它。但实测这个邮箱里有几封（运营商通知类）
    没有 Message-ID 或格式不合法，此时回退到 Date+Subject+From 的哈希。
    注意不能用 UID 兜底：INBOX 和标签文件夹是两个独立的 UID 空间。
    """
    message_id = headers.get("message-id", "").strip()
    if message_id:
        return message_id
    basis = "\x1f".join(
        headers.get(field, "").strip() for field in ("date", "subject", "from", "to")
    )
    return "h:" + hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()


# --------------------------------------------------------------------------- #
# 连接与 IMAP 底层工具
# --------------------------------------------------------------------------- #


def _qg(value: str) -> str:
    """把字符串包装成 IMAP 带引号参数。"""
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def _chunks(items: list[int], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _uid_set(uids: list[int]) -> str:
    """把 UID 列表压成 IMAP 序列集，例如 [20,21,22,25] -> '20:22,25'。"""
    ordered = sorted(set(uids))
    if not ordered:
        return ""
    parts: list[str] = []
    start = previous = ordered[0]
    for uid in ordered[1:]:
        if uid == previous + 1:
            previous = uid
            continue
        parts.append(str(start) if start == previous else f"{start}:{previous}")
        start = previous = uid
    parts.append(str(start) if start == previous else f"{start}:{previous}")
    return ",".join(parts)


def _uids(conn: imaplib.IMAP4_SSL, *criteria: str | None) -> list[int]:
    """执行 UID SEARCH，返回升序 UID 列表。"""
    typ, data = conn.uid("SEARCH", *criteria)
    if typ != "OK" or not data or not data[0]:
        return []
    return sorted(int(value) for value in data[0].split())


def _first_literal(data) -> bytes | None:
    """从 FETCH 响应里取出第一个 literal（邮件原文）。"""
    for item in data:
        if isinstance(item, tuple) and len(item) == 2:
            return item[1]
    return None


def _fetch_headers(
    conn: imaplib.IMAP4_SSL, uids: list[int], fields: str
) -> dict[int, dict[str, str]]:
    """批量取 UID -> {小写头名: 值}。

    QQ 邮箱的 IMAP SEARCH 会忽略搜索条件（搜 SUBJECT 也返回全部邮件，
    搜 HEADER MESSAGE-ID 又返回 0 封），所以凡是「按头筛选」都必须
    自己抓下来在本地比对，不能交给服务器。
    """
    result: dict[int, dict[str, str]] = {}
    for chunk in _chunks(uids, 60):
        typ, data = "NO", None
        for attempt in range(3):
            typ, data = conn.uid(
                "FETCH",
                ",".join(str(uid) for uid in chunk),
                f"(BODY.PEEK[HEADER.FIELDS ({fields})])",
            )
            if typ == "OK":
                break
            time.sleep(0.5 * (attempt + 1))
        if typ != "OK" or not data:
            # 不能静默跳过：少一个分块就会把「已处理」误判成「未处理」，
            # 表现为已标记的邮件复活、被重复回复。
            logger.warning("取邮件头失败，分块 %s..%s", chunk[0], chunk[-1])
            continue
        pending: int | None = None
        for item in data:
            if isinstance(item, tuple) and len(item) == 2:
                prefix, literal = item
                match = _FETCH_UID.search(prefix)
                uid = int(match.group(1)) if match else pending
                if uid is not None:
                    headers: dict[str, str] = {}
                    for name, value in email.message_from_bytes(literal).items():
                        headers.setdefault(name.lower(), " ".join(str(value).split()))
                    result[uid] = headers
                pending = None
            elif isinstance(item, bytes):
                match = _FETCH_UID.search(item)
                if match:
                    pending = int(match.group(1))
    return result


def _fetch_raw_many(conn: imaplib.IMAP4_SSL, uids: list[int]) -> dict[int, bytes]:
    """批量取 UID -> 邮件原文。"""
    result: dict[int, bytes] = {}
    for chunk in _chunks(uids, 10):  # 正文大，块开小一点
        typ, data = conn.uid("FETCH", ",".join(str(uid) for uid in chunk), "(BODY.PEEK[])")
        if typ != "OK" or not data:
            continue
        pending: int | None = None
        for item in data:
            if isinstance(item, tuple) and len(item) == 2:
                prefix, literal = item
                match = _FETCH_UID.search(prefix)
                uid = int(match.group(1)) if match else pending
                if uid is not None:
                    result[uid] = literal
                pending = None
            elif isinstance(item, bytes):
                match = _FETCH_UID.search(item)
                if match:
                    pending = int(match.group(1))
    return result


# --------------------------------------------------------------------------- #
# MIME 解析
# --------------------------------------------------------------------------- #


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (UnicodeDecodeError, LookupError):
        return value.strip()


def _part_text(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charsets = [part.get_content_charset(), "utf-8", "gb18030"]
    for charset in charsets:
        if not charset:
            continue
        try:
            return payload.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _html_to_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = _HTML_TAG.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", unescape(text)).strip()


def _extract_body(message) -> str:
    plain: list[str] = []
    rich: list[str] = []
    for part in message.walk():
        if part.get_filename():
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain.append(_part_text(part))
        elif content_type == "text/html":
            rich.append(_part_text(part))
    if any(chunk.strip() for chunk in plain):
        return "\n".join(chunk for chunk in plain if chunk.strip()).strip()
    if any(chunk.strip() for chunk in rich):
        return _html_to_text("\n".join(rich))
    return ""


def _has_attachments(message) -> bool:
    return any(part.get_filename() for part in message.walk())


def _date_timestamp(headers: dict[str, str]) -> float:
    try:
        return parsedate_to_datetime(headers.get("date", "")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _thread_key(headers: dict[str, str]) -> str:
    references = headers.get("references", "").split()
    if references:
        return references[0]
    return _identity(headers)


def _parse_message(raw: bytes, uid: str) -> dict:
    """把原始邮件解析成与 Gmail 版字段对齐的 dict。"""
    message = email.message_from_bytes(raw)
    headers: dict[str, str] = {}
    for name, value in message.items():
        key = name.lower()
        if key not in headers:
            headers[key] = _decode_header_value(value)

    body = _extract_body(message)
    # 课程的 guards 会检查 labelIds 里有没有 "SENT" 来识别「自己发的邮件」。
    # IMAP 没有标签体系，但发件人是我们自己就等于 SENT，这里补上这个语义，
    # 否则 send_reply 追加回 INBOX 的副本会绕过第一道 guard。
    sender = parseaddr(headers.get("from", ""))[1].lower()
    labels = ["SENT"] if sender and sender == settings.mail_address.lower() else []
    return {
        "id": uid,
        "_key": _identity(headers),
        "threadId": _thread_key(headers),
        "labelIds": labels,
        "snippet": re.sub(r"\s+", " ", body)[:120],
        "headers": headers,
        "body": body,
        "has_attachments": _has_attachments(message),
        "date_ts": _date_timestamp(headers),
    }


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #


def _with_reconnect(method):
    """IMAP 长连接会被服务端断掉（实测 QQ 会在命令执行中途 EOF）。

    `_imap()` 里虽然有 noop 探活，但那只覆盖「命令还没发」的情况；
    命令已经开始、socket 才断的话，异常会直接冒到调用方。
    这个装饰器在中断时关掉旧连接、重连、重试一次。
    只用于幂等操作——send_reply 不能用，否则 SMTP 已发出时会重复发信。
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except (imaplib.IMAP4.abort, OSError) as error:
            logger.warning("IMAP 连接中断（%s），重连后重试 %s", error, method.__name__)
            self.close()
            return method(self, *args, **kwargs)

    return wrapper


class Gmail:
    """名字沿用课程的 `Gmail`，实现换成 IMAP/SMTP（默认 QQ 邮箱）。"""

    def __init__(self) -> None:
        # 先声明连接字段：__init__ 中途抛异常时 __del__ 仍会被调用。
        self._conn: imaplib.IMAP4_SSL | None = None
        self.address: str = settings.mail_address
        self.label_ids: dict[str, str] = {}
        self._label_cache: dict[str, set[str]] = {}
        if not settings.mail_address or not settings.mail_auth_code:
            raise RuntimeError(
                "MAIL_ADDRESS / MAIL_AUTH_CODE 还没设置。"
                "去 QQ 邮箱「设置 → 账号与安全 → POP3/IMAP/SMTP 服务」开启 IMAP，"
                "拿到 16 位授权码后写进 .env。注意是授权码，不是登录密码。"
            )
        self.label_ids = self._ensure_labels()

    # -- 连接 --------------------------------------------------------------- #

    def _imap(self) -> imaplib.IMAP4_SSL:
        if self._conn is not None:
            try:
                self._conn.noop()
                return self._conn
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError):
                self.close()
        conn = imaplib.IMAP4_SSL(
            settings.imap_host,
            settings.imap_port,
            ssl_context=ssl.create_default_context(),
            timeout=60,
        )
        conn.login(self.address, settings.mail_auth_code)
        try:  # 163/126 需要，QQ 会忽略
            conn.xatom("ID", '("name" "ai-customer-support" "version" "1.0")')
        except (imaplib.IMAP4.error, OSError):
            pass
        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:  # noqa: BLE001 - 关闭失败不该掩盖真正的错误
                pass
            self._conn = None

    # IMAP 是有状态的连接，不像 Gmail API 那样每次请求都是独立的 HTTP 调用。
    # QQ 邮箱对同一账号的并发 IMAP 连接数有限制，所以 poller 每轮结束必须断开。
    # 课程里的 poller 是 `gmail = Gmail()` 然后不管，在 Gmail 版没问题，
    # 在 IMAP 版会一直累积连接，几小时后就会被服务器拒连。
    def __enter__(self) -> "Gmail":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __del__(self) -> None:
        # 兜底：即使调用方忘了 close，对象被回收时也断开。
        try:
            self.close()
        except Exception:  # noqa: BLE001 - 解释器退出阶段可能已经拆了 socket
            pass

    def _select(self, folder: str, readonly: bool = True) -> None:
        conn = self._imap()
        mailbox = folder if _FOLDER_SAFE.match(folder) else _qg(folder)
        typ, data = conn.select(mailbox, readonly=readonly)
        if typ != "OK":
            raise RuntimeError(f"无法打开文件夹 {folder!r}：{data!r}")

    # -- 标签（IMAP 文件夹） -------------------------------------------------- #

    def _ensure_labels(self) -> dict[str, str]:
        conn = self._imap()
        typ, data = conn.list()
        existing: set[str] = set()
        if typ == "OK":
            for entry in data or []:
                if not entry:
                    continue
                text = entry.decode(errors="replace") if isinstance(entry, bytes) else str(entry)
                quoted = re.findall(r'"([^"]*)"', text)
                if quoted:
                    existing.add(quoted[-1])
                else:
                    existing.add(text.split()[-1])
        ids: dict[str, str] = {}
        for name in (PROCESSED_LABEL, ESCALATED_LABEL):
            if name not in existing:
                conn.create(name if _FOLDER_SAFE.match(name) else _qg(name))
            ids[name] = name
        return ids

    def _labelled_message_ids(self, label: str, limit: int | None = None) -> set[str]:
        """取标签文件夹里所有邮件的身份集合。

        这里**不能**只取「最后 N 封」：标签副本的追加顺序和收件箱顺序并不一致
        （先标记的旧邮件会落在文件夹底部），截断窗口会把它们漏掉，
        于是已经被处理过的邮件又被当成新工单——表现为「处理过的邮件复活」。
        身份集合在一次 Gmail 实例内缓存，因为一轮 poll 里会被查好几遍。
        """
        if label in self._label_cache:
            return self._label_cache[label]
        self._select(label)
        uids = _uids(self._imap(), "ALL")
        if limit:
            uids = uids[-limit:]
        if not uids:
            self._label_cache[label] = set()
            return set()
        headers = _fetch_headers(self._imap(), uids, _HEADER_FIELDS)
        identities = {_identity(value) for value in headers.values()}
        self._label_cache[label] = identities
        return identities

    @_with_reconnect
    def add_label(self, message_id: str, name: str) -> None:
        if name not in self.label_ids:
            raise KeyError(f"未知标签 {name!r}")
        folder = self.label_ids[name]
        self._select("INBOX", readonly=False)
        typ, data = self._imap().uid("COPY", str(message_id), folder if _FOLDER_SAFE.match(folder) else _qg(folder))
        if typ != "OK":
            raise RuntimeError(f"无法给 {message_id} 加标签 {name}：{data!r}")
        self._label_cache.pop(name, None)

    @_with_reconnect
    def add_label_many(self, message_ids, name: str) -> int:
        """批量打标签，用 UID 序列集一次提交，比逐个 add_label 快得多。

        用途：poller 上线前把收件箱里已有的历史邮件一次性标成已处理，
        避免机器人把它们当新工单、挨个回复。
        """
        if name not in self.label_ids:
            raise KeyError(f"未知标签 {name!r}")
        folder = self.label_ids[name]
        uids = sorted({int(value) for value in message_ids})
        if not uids:
            return 0
        self._select("INBOX", readonly=False)
        for chunk in _chunks(uids, 200):
            typ, data = self._imap().uid("COPY", _uid_set(chunk), folder)
            if typ != "OK":
                raise RuntimeError(f"批量打标签失败：{data!r}")
        self._label_cache.pop(name, None)
        return len(uids)

    # -- 读 ----------------------------------------------------------------- #

    @_with_reconnect
    def list_new_message_ids(self, max_results: int = 10) -> list[str]:
        """返回收件箱里还没打 ai-processed 标签的邮件，按到达顺序（旧→新）。"""
        conn = self._imap()
        window = max(max_results * 5, 50)
        self._select("INBOX")
        uids = _uids(conn, "ALL")[-window:]
        if not uids:
            return []
        headers = _fetch_headers(conn, uids, _HEADER_FIELDS)
        processed = self._labelled_message_ids(PROCESSED_LABEL)
        fresh = [
            uid
            for uid in uids
            if _identity(headers.get(uid, {})) not in processed
        ]
        return [str(uid) for uid in fresh[-max_results:]]

    def _fetch_raw(self, uid: str) -> bytes | None:
        self._select("INBOX")
        return _fetch_raw_many(self._imap(), [int(uid)]).get(int(uid))

    @_with_reconnect
    def get_message(self, message_id: str) -> dict:
        raw = self._fetch_raw(str(message_id))
        if raw is None:
            raise KeyError(f"收件箱里找不到邮件 {message_id}")
        return _parse_message(raw, str(message_id))

    @_with_reconnect
    def get_thread(self, thread_id: str, window: int = 300) -> dict:
        """按会话根 Message-ID 找回整条会话。

        QQ 邮箱的 SEARCH 会忽略条件，所以不能 `SEARCH HEADER REFERENCES`，
        只能把最近 window 封邮件的头抓下来本地比对。
        """
        conn = self._imap()
        self._select("INBOX")
        uids = _uids(conn, "ALL")[-window:]
        if not uids:
            return {"id": thread_id, "messages": []}
        headers = _fetch_headers(conn, uids, _HEADER_FIELDS)
        matching = [
            uid
            for uid, value in headers.items()
            if _identity(value) == thread_id
            or thread_id in value.get("references", "").split()
        ]
        raw_messages = _fetch_raw_many(conn, sorted(matching))
        messages = [
            _parse_message(raw, str(uid)) for uid, raw in sorted(raw_messages.items())
        ]
        messages.sort(key=lambda item: item["date_ts"])
        return {"id": thread_id, "messages": messages}

    @_with_reconnect
    def is_escalated(self, thread: dict) -> bool:
        identity_keys = {
            message.get("_key") or _identity(get_headers(message))
            for message in thread.get("messages", [])
        }
        identity_keys.discard(None)
        if not identity_keys:
            return False
        labelled = self._labelled_message_ids(ESCALATED_LABEL)
        return bool(identity_keys & labelled)

    # -- 发 ----------------------------------------------------------------- #

    def _append(self, folder: str, raw: bytes) -> None:
        mailbox = folder if _FOLDER_SAFE.match(folder) else _qg(folder)
        try:
            self._imap().append(mailbox, r"(\Seen)", imaplib.Time2Internaldate(time.time()), raw)
        except (imaplib.IMAP4.error, OSError):
            # 存副本失败不应该让「已经发出去的回复」变成异常
            pass

    def send_reply(self, message: dict, body: str) -> dict:
        headers = get_headers(message)
        recipient = headers.get("reply-to") or headers.get("from", "")
        if not recipient:
            raise ValueError("这封邮件没有 From / Reply-To 头，无法回复")
        subject = headers.get("subject", "")
        reply = EmailMessage()
        reply["From"] = self.address
        reply["To"] = recipient
        reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        parent_id = headers.get("message-id")
        if parent_id:
            reply["In-Reply-To"] = parent_id
            reply["References"] = f"{headers.get('references', '')} {parent_id}".strip()
        reply["Message-ID"] = make_msgid(domain=self.address.split("@")[-1] or None)
        reply["Date"] = formatdate(localtime=True)
        reply.set_content(body)

        # 465 走隐式 SSL，587 走 STARTTLS，两种都支持，省得网络环境不同时再改代码。
        if settings.smtp_port == 587:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=60) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self.address, settings.mail_auth_code)
                smtp.send_message(
                    reply,
                    from_addr=self.address,
                    to_addrs=[address.strip() for address in recipient.split(",") if address.strip()],
                )
        else:
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                context=ssl.create_default_context(),
                timeout=60,
            ) as smtp:
                smtp.login(self.address, settings.mail_auth_code)
                smtp.send_message(
                    reply,
                    from_addr=self.address,
                    to_addrs=[address.strip() for address in recipient.split(",") if address.strip()],
                )

        raw = reply.as_bytes()
        # 一份进收件箱，让 format_thread 能看到自己说过的话；
        # 一份进 ai-processed，让 poller 不会把它当成新邮件再处理一遍。
        self._append("INBOX", raw)
        self._append(PROCESSED_LABEL, raw)
        return {"id": reply["Message-ID"], "threadId": message.get("threadId", "")}


# --------------------------------------------------------------------------- #
# 与课程同名同签名的模块级函数
# --------------------------------------------------------------------------- #


def get_headers(message: dict) -> dict[str, str]:
    return dict(message.get("headers", {}))


def get_body(message: dict) -> str:
    return message.get("body", "")


def has_attachments(message: dict) -> bool:
    return bool(message.get("has_attachments", False))


def strip_quoted(text: str) -> str:
    """去掉邮件客户端自动附带的被引用历史。"""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("on ") and lowered.endswith("wrote:"):
            break
        if stripped.startswith("在 ") and "写道" in stripped:
            break
        if any(lowered.startswith(tag) for tag in _QUOTE_TAGS):
            break
        if stripped.startswith(">"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def format_thread(thread: dict, own_address: str) -> str:
    """把整个会话渲染成纯文本，最早的邮件在前。"""
    messages = thread.get("messages") or []
    if not messages:
        return ""
    first = get_headers(messages[0])
    parts = [f"Subject: {first.get('subject', '')}"]
    for message in messages:
        sender = get_headers(message).get("from", "")
        role = "Harbor & Pine support" if own_address.lower() in sender.lower() else "Customer"
        parts.append(f"{role} ({sender}):\n{strip_quoted(get_body(message))}")
    return "\n\n".join(parts)
