"""把收件箱里已有的历史邮件一次性标成 ai-processed，避免 poller 当成新工单。"""

from __future__ import annotations

import argparse
import sys
import time

from app.gmail import PROCESSED_LABEL, Gmail, get_headers


def main() -> int:
    parser = argparse.ArgumentParser(description="把已有邮件标成 ai-processed")
    parser.add_argument("--yes", action="store_true", help="真的执行（不加就是预演）")
    parser.add_argument("--keep-last", type=int, default=0, help="保留最近 N 封不标记，默认 0")
    parser.add_argument("--show", type=int, default=8, help="预览最近几封的标题，默认 8")
    args = parser.parse_args()

    try:
        gmail = Gmail()
    except RuntimeError as error:
        print(f"配置未完成: {error}")
        return 1

    with gmail:
        unprocessed = gmail.list_new_message_ids(max_results=5000)
        print(f"邮箱: {gmail.address}")
        print(f"未处理邮件: {len(unprocessed)} 封")
        if not unprocessed:
            print("已经没有未处理邮件了，poller 可以干净启动。")
            return 0

        print(f"\n最近 {min(args.show, len(unprocessed))} 封的标题（用来确认这些确实该跳过）：")
        for uid in unprocessed[-args.show :]:
            message = gmail.get_message(uid)
            headers = get_headers(message)
            sender = (headers.get("from") or "")[:40]
            subject = (headers.get("subject") or "(无主题)")[:50]
            print(f"  UID {uid:>4}  {sender:<40}  {subject}")

        keep = max(args.keep_last, 0)
        to_mark = unprocessed[: len(unprocessed) - keep] if keep else unprocessed
        print(f"\n将要标记: {len(to_mark)} 封；保留未处理: {len(unprocessed) - len(to_mark)} 封")

        if not args.yes:
            print("\n这是预演，什么都没改。确认无误后加 --yes 执行。")
            return 0

        marked = gmail.add_label_many(to_mark, PROCESSED_LABEL)
        print(f"已标记 {marked} 封为 {PROCESSED_LABEL}")

        # QQ 的 COPY 索引有延迟，立刻回查可能还是旧状态，所以重试几次。
        remaining: list[str] = []
        for attempt in range(1, 4):
            time.sleep(3)
            remaining = gmail.list_new_message_ids(max_results=5000)
            remain_set = set(remaining)
            leftover = [uid for uid in to_mark if uid in remain_set]
            print(f"  第 {attempt} 次回查: 未处理 {len(remaining)} 封，其中本次应标记的还剩 {len(leftover)} 封")
            if not leftover:
                break
        print(f"现在未处理邮件: {len(remaining)} 封 -> {remaining[:10]}")
        if remaining:
            print("\n剩下这些是 --keep-last 有意保留的，poller 会处理它们。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

