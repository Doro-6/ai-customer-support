"""一封信的完整处理：护栏、认领、入库、AI 决策、回复或转人工、打标签。"""

import logging
from email.utils import parseaddr

from app import db
from app.ai.decide import decide
from app.config import settings
from app.gmail import (
    ESCALATED_LABEL,
    PROCESSED_LABEL,
    Gmail,
    format_thread,
    get_body,
    get_headers,
    has_attachments,
)
from app.guards import skip_reason

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


def handle_message(gmail: Gmail, message_id: str) -> None:
    message = gmail.get_message(message_id)
    key = message.get("_key") or str(message_id)

    event = db.start_event(key)
    if event is None:
        # 已经处理过，或在处理中。打上标签，保证不会每轮重复扫。
        gmail.add_label(message_id, PROCESSED_LABEL)
        return

    try:
        status, result = process(gmail, message_id, message, key)
        gmail.add_label(message_id, PROCESSED_LABEL)
        db.finish_event(event["id"], status, result)
        logger.info("%s %s %s", message_id, status, result)
    except Exception as error:
        db.fail_event(event["id"], str(error))
        if event["attempts"] >= MAX_ATTEMPTS:
            logger.error("%s 连续失败 %s 次，放弃", message_id, event["attempts"])
            gmail.add_label(message_id, PROCESSED_LABEL)
        raise


def process(
    gmail: Gmail, message_id: str, message: dict, key: str
) -> tuple[str, dict]:
    headers = get_headers(message)

    reason = skip_reason(message, headers, gmail.address)
    if reason:
        return "skipped", {"reason": reason}

    sender = parseaddr(headers.get("from", ""))[1]
    ticket_id = db.upsert_ticket(message["threadId"], sender, headers.get("subject", ""))
    db.save_message(ticket_id, key, "in", sender, get_body(message))

    thread = gmail.get_thread(message["threadId"])
    if gmail.is_escalated(thread):
        # 同事在处理这个会话，直到他们在邮箱里把 needs-human 文件夹清掉。
        return "skipped", {"reason": "thread is escalated"}

    decision = decide(format_thread(thread, gmail.address), has_attachments(message))

    if settings.pipeline_dry_run:
        return "skipped", {"reason": "dry-run", "decision": decision.model_dump()}

    sent = gmail.send_reply(message, decision.reply)
    db.save_message(ticket_id, sent["id"], "out", gmail.address, decision.reply)

    if decision.action == "escalate":
        gmail.add_label(message_id, ESCALATED_LABEL)
        db.update_ticket(
            ticket_id, "escalated", decision.section, decision.proposed_action
        )
    else:
        db.update_ticket(ticket_id, "answered", decision.section)

    return "processed", decision.model_dump()
