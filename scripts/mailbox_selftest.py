"""离线自检：不连网络，验证 MIME 解析 / 引文剥离 / 会话渲染是否正确。

等价于课程里 `scripts/gmail_check.py` 的「解析那一半」，
但不需要任何凭据就能跑：

    uv run python -m scripts.mailbox_selftest
"""

from email.message import EmailMessage
from email.utils import formatdate

from app.gmail import (
    format_thread,
    get_body,
    get_headers,
    has_attachments,
    strip_quoted,
)
from app.gmail import _parse_message  # 内部解析函数，这里专门测它
from app.gmail import _reply_subject  # 同上：回复主题的构造逻辑
from app.guards import skip_reason

OWN = "harborandpine.support@qq.com"
CUSTOMER = "Customer <customer@example.com>"

failures: list[str] = []


def check(name: str, actual, expected) -> None:
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"      expected: {expected!r}")
        print(f"      actual  : {actual!r}")
        failures.append(name)


def build_message() -> bytes:
    """造一封真实形状的邮件：GB2312 正文 + 中文主题 + 附件 + 被引用历史。"""
    message = EmailMessage()
    message["From"] = CUSTOMER
    message["To"] = OWN
    message["Subject"] = "板子漏气怎么办"
    message["Message-ID"] = "<child@example.com>"
    message["References"] = "<root@example.com> <mid@example.com>"
    message["Date"] = formatdate(localtime=True)
    message.set_content(
        "你好，我的板子每天早上都变软，是不是漏气？\n"
        "\n"
        "> 上一封邮件里我说过要换货\n"
        "On Mon, 29 Sep 2026 at 10:00, support wrote:\n"
        "旧的回复内容\n",
        charset="gb2312",
    )
    message.add_attachment(
        b"%PDF-1.4 fake",
        maintype="application",
        subtype="pdf",
        filename="policy.pdf",
    )
    return message.as_bytes()


def main() -> int:
    parsed = _parse_message(build_message(), "42")

    check("id 用 IMAP UID", parsed["id"], "42")
    check("threadId 取 References 第一个", parsed["threadId"], "<root@example.com>")
    check("主题按 GB2312 解码", get_headers(parsed)["subject"], "板子漏气怎么办")
    check("发件人头", get_headers(parsed)["from"], CUSTOMER)
    check("中文正文可读", "每天早上都变软" in get_body(parsed), True)
    check("附件被识别", has_attachments(parsed), True)

    cleaned = strip_quoted(get_body(parsed))
    check("引文 > 行被去掉", "上一封邮件里我说过要换货" in cleaned, False)
    check("引文 On ... wrote: 之后被截断", "旧的回复内容" in cleaned, False)
    check("正常内容保留", "是不是漏气" in cleaned, True)

    thread = {
        "id": "<root@example.com>",
        "messages": [
            {
                "id": "1",
                "threadId": "<root@example.com>",
                "headers": {
                    "from": CUSTOMER,
                    "subject": "板子漏气怎么办",
                    "message-id": "<root@example.com>",
                },
                "body": "我的板子漏气",
                "has_attachments": False,
                "date_ts": 1.0,
            },
            {
                "id": "2",
                "threadId": "<root@example.com>",
                "headers": {
                    "from": "Harbor & Pine support <%s>" % OWN,
                    "subject": "Re: 板子漏气怎么办",
                    "message-id": "<child@example.com>",
                },
                "body": "请先做气阀检查",
                "has_attachments": False,
                "date_ts": 2.0,
            },
        ],
    }
    rendered = format_thread(thread, OWN)
    print("\n--- format_thread 渲染结果 ---")
    print(rendered)
    print("--- 结束 ---\n")
    check("会话带 Subject 首行", rendered.startswith("Subject: 板子漏气怎么办"), True)
    check("客户邮件标为 Customer", "Customer (Customer <customer@example.com>)" in rendered, True)
    check("自家回复标为 support", "Harbor & Pine support (Harbor & Pine support" in rendered, True)
    check("两封都在", rendered.count(":") >= 2, True)

    normal = {"labelIds": []}
    check("护栏：自己发出的邮件被拦", skip_reason({"labelIds": ["SENT"]}, {}, OWN), "sent by us")
    check(
        "护栏：发件人是自己",
        skip_reason(normal, {"from": f"Harbor & Pine support <{OWN}>"}, OWN),
        "from our own address",
    )
    check(
        "护栏：自动回复（RFC 3834）",
        skip_reason(normal, {"from": "a@b.com", "auto-submitted": "auto-replied"}, OWN),
        "auto-submitted: auto-replied",
    )
    check(
        "护栏：群发/邮件列表",
        skip_reason(normal, {"from": "a@b.com", "precedence": "bulk"}, OWN),
        "bulk or list mail",
    )
    check(
        "护栏：正常客户邮件必须放行",
        skip_reason(normal, {"from": "customer@example.com"}, OWN),
        None,
    )
    check(
        "护栏：大小写不影响发件人判断",
        skip_reason(normal, {"from": f"X <{OWN.upper()}>"}, OWN),
        "from our own address",
    )

    check("主题：正常主题加 Re:", _reply_subject("Where is my pump?"), "Re: Where is my pump?")
    check("主题：已带 Re: 不重复加", _reply_subject("Re: Where is my pump?"), "Re: Where is my pump?")
    check("主题：大小写 Re: 也算", _reply_subject("RE: hi"), "RE: hi")
    check("主题：空主题用兜底", _reply_subject(""), "Re: Harbor & Pine support")
    check("主题：纯空格用兜底", _reply_subject("   "), "Re: Harbor & Pine support")
    check(
        "主题：QQ 的 (无主题) 占位符换成兜底",
        _reply_subject("(无主题)"),
        "Re: Harbor & Pine support",
    )
    check(
        "主题：英文占位符同样处理",
        _reply_subject("(no subject)"),
        "Re: Harbor & Pine support",
    )
    check("主题：None 也不炸", _reply_subject(None), "Re: Harbor & Pine support")

    print()
    if failures:
        print(f"{len(failures)} 项失败：{failures}")
        return 1
    print("全部通过：解析层 + 护栏可用（这一步不需要网络和凭据）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
