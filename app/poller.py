"""轮询进程：每 POLL_INTERVAL_SECONDS 秒扫一次收件箱。"""

import logging
import time

from app.config import settings
from app.gmail import Gmail
from app.pipeline import handle_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("poller")


def poll_once() -> int:
    with Gmail() as gmail:
        message_ids = gmail.list_new_message_ids()
        for message_id in message_ids:
            try:
                handle_message(gmail, message_id)
            except Exception:
                # 一封坏邮件不能拖垮整个循环
                logger.exception("Failed to process %s", message_id)
        return len(message_ids)


def main() -> None:
    logger.info("Polling every %s seconds", settings.poll_interval_seconds)
    while True:
        try:
            count = poll_once()
            if count:
                logger.info("Handled %s new messages", count)
        except Exception:
            logger.exception("Poll failed")
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
