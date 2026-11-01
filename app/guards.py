"""纯 Python 护栏：决定哪些邮件绝对不能回。"""

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
