"""IMAP + SMTP 邮箱客户端，对外接口与课程的 Gmail 版一致。"""

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

# 有些服务器（实测 QQ）发信人没写主题时，会把字面量占位符写进 Subject 头，
# 而不是留空。原样加个 "Re: " 拼回去就变成 "Re: (无主题)"——既难看，
# 也容易被反垃圾策略当作畸形主题拦掉。这些占位符一律换成正常主题。
_NO_SUBJECT_PLACEHOLDERS = {
    "(无主题)",
    "无主题",
    "(無主題)",
    "無主題",
    "(无标题)",
    "无标题",
    "(no subject)",
    "no subject",
    "(no title)",
    "(empty)",
    "(空)",
}

_SUBJECT_FALLBACK = "Harbor & Pine support"


def _reply_subject(original: str) -> str:
    """构造回复主题：空主题或占位符用兜底主题，已带 Re: 则不重复加。"""
    subject = (original or "").strip()
    if not subject or subject.lower() in _NO_SUBJECT_PLACEHOLDERS:
        subject = _SUBJECT_FALLBACK
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"

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

_HEADER_FIELDS = "MESSAGE-ID REFERENCES DATE SUBJECT FROM TO"


def _identity(headers: dict[str, str]) -> str:
    """邮件的稳定身份：优先 Message-ID，缺失时回退 Date+Subject+From 哈希。"""
    message_id = headers.get("message-id", "").strip()
    if message_id:
        return message_id
    basis = "\x1f".join(
        headers.get(field, "").strip() for field in ("date", "subject", "from", "to")
    )
    return "h:" + hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()




def _qg(value: str) -> str:
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def _chunks(items: list[int], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _uid_set(uids: list[int]) -> str:
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
    typ, data = conn.uid("SEARCH", *criteria)
    if typ != "OK" or not data or not data[0]:
        return []
    return sorted(int(value) for value in data[0].split())


def _first_literal(data) -> bytes | None:
    for item in data:
        if isinstance(item, tuple) and len(item) == 2:
            return item[1]
    return None


def _fetch_headers(
    conn: imaplib.IMAP4_SSL, uids: list[int], fields: str
) -> dict[int, dict[str, str]]:
    """批量取 UID -> 头字典。QQ 的 IMAP 会忽略搜索条件，所以筛选都在本地做。"""
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




def _with_reconnect(method):
    """断线自动重连并重试一次。仅用于幂等操作，send_reply 不能重试。"""

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
        """取标签文件夹里所有邮件的身份。必须全量扫描：截断窗口会让已处理的邮件复活。"""
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
        """批量打标签，用 UID 序列集一次提交。"""
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


    @_with_reconnect
    def list_new_message_ids(self, max_results: int = 10) -> list[str]:
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
        """按会话根 Message-ID 找回整条会话（客户端筛选，服务器搜索不可靠）。"""
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
        reply = EmailMessage()
        reply["From"] = self.address
        reply["To"] = recipient
        reply["Subject"] = _reply_subject(headers.get("subject", ""))
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




def get_headers(message: dict) -> dict[str, str]:
    return dict(message.get("headers", {}))


def get_body(message: dict) -> str:
    return message.get("body", "")


def has_attachments(message: dict) -> bool:
    return bool(message.get("has_attachments", False))


def strip_quoted(text: str) -> str:
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
