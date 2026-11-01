"""换货规则：模型只报告事实，Python 决定下一步。

这套逻辑是「模型提议、代码决定」的落点。没有订单系统，所以什么都不会真的发货；
算出来的 step 会写进 ticket 的 proposed_action，由同事去执行。
"""

from app.ai.schemas import Issue

# 这两步回复直接发给客户：因为客户自己必须先检查或补充信息。
CUSTOMER_STEPS = {"valve-check", "ask-details"}


def resolve_issue(issue: Issue) -> str:
    """决定一个报障邮件的下一步。模型报告，Python 决定。"""
    step = next_step(issue)
    if step not in CUSTOMER_STEPS | {"escalate"} and not issue.order_number:
        return "ask-details"
    return step


def next_step(issue: Issue) -> str:
    if issue.product == "paddle-board":
        if issue.problem == "missing-accessory":
            return "ship-part"
        if issue.problem == "slow-leak":
            if not issue.valve_checked:
                return "valve-check"
            if issue.days_since_delivery is None:
                return "ask-details"
            return "new-board" if issue.days_since_delivery <= 30 else "patch-kit"
    if issue.product == "shelving":
        if issue.problem == "missing-hardware":
            return "ship-hardware-kit"
        if issue.problem == "wrong-finish":
            return "exchange"
        if issue.problem == "damaged-frame":
            return "full-replacement"
        if issue.problem == "damaged-panels":
            if not issue.part_numbers:
                return "ask-details"
            return "ship-panels" if len(issue.part_numbers) <= 2 else "full-replacement"
    # 接缝开裂和任何意外情况都转人工。
    return "escalate"
