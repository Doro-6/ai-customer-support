"""Bot 的护栏：纯 Python 决定「这封信绝对不能回」。

放在 AI 之前运行，和课程 app/guards.py 的逻辑保持一致。没有这一层，
两个自动回复系统会互相回信直到有人发现——这不是假设，是真实事故。

四个条件（任一命中就不回）：
1. 自己发出的邮件（labelIds 含 SENT）
2. 发件人就是自己的地址
3. Auto-Submitted 不是 no（自动回复、退信，RFC 3834）
4. Precedence 是 bulk / list / junk（群发、邮件列表）

注意第 1 条对我们的 IMAP 实现是必需的，不是可选：
`send_reply` 会把回复副本 APPEND 回 INBOX，靠这个标记才能识别出来。
"""

from email.utils import parseaddr


def skip_reason(message: dict, headers: dict[str, str], own_address: str) -> str | None:
    """返回「为什么不能回这封信」，可以回则返回 None。"""
    if "SENT" in message.get("labelIds", []):
        return "sent by us"

    if parseaddr(headers.get("from", ""))[1].lower() == own_address.lower():
        return "from our own address"

    auto_submitted = headers.get("auto-submitted", "no").strip().lower()
    if auto_submitted != "no":
        return f"auto-submitted: {auto_submitted}"

    if headers.get("precedence", "").strip().lower() in {"bulk", "list", "junk"}:
        return "bulk or list mail"

    return None
