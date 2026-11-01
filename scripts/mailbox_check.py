"""连真实邮箱做一次端到端检查，需要 .env 里的邮箱凭据。"""

from __future__ import annotations

import argparse
import sys

from app.gmail import ESCALATED_LABEL, PROCESSED_LABEL, Gmail, format_thread, get_body, get_headers, has_attachments


def show_folders(gmail: Gmail) -> None:
    print(f"标签文件夹: {gmail.label_ids}")
    labels = gmail.label_ids
    assert PROCESSED_LABEL in labels and ESCALATED_LABEL in labels


def main() -> int:
    parser = argparse.ArgumentParser(description="IMAP 邮箱连通性与读写检查")
    parser.add_argument("--uid", help="要检查的 IMAP UID，默认取最新一封新邮件")
    parser.add_argument("--mark", action="store_true", help="给这封邮件打 ai-processed 标签")
    parser.add_argument("--reply", action="store_true", help="给这封邮件发一封测试回复")
    parser.add_argument("--limit", type=int, default=5, help="最多列几封新邮件，默认 5")
    args = parser.parse_args()

    print("连接中 ...")
    try:
        gmail = Gmail()
    except RuntimeError as error:
        print(f"\n配置未完成: {error}")
        return 1
    except OSError as error:
        print(f"\n连不上邮箱服务器: {error}")
        print("检查 IMAP_HOST / IMAP_PORT，以及网络是否放行 993 端口。")
        return 1
    print(f"已登录: {gmail.address}")
    show_folders(gmail)

    if args.uid:
        message_id = args.uid
    else:
        new_ids = gmail.list_new_message_ids(max_results=args.limit)
        print(f"未处理的新邮件: {len(new_ids)} 封 -> {new_ids}")
        if not new_ids:
            print(
                "\n收件箱里没有可处理的新邮件。\n"
                f"先往 {gmail.address} 发一封邮件（用你另一个邮箱，或自己给自己发），再跑一次。"
            )
            return 0
        message_id = new_ids[-1]

    message = gmail.get_message(message_id)
    headers = get_headers(message)
    print(f"\n--- 邮件 UID {message_id} ---")
    print(f"From    : {headers.get('from')}")
    print(f"Subject : {headers.get('subject')}")
    print(f"Date    : {headers.get('date')}")
    print(f"Message-ID: {headers.get('message-id')}")
    print(f"threadId: {message['threadId']}")
    print(f"附件    : {has_attachments(message)}")
    print(f"\n正文前 800 字:\n{get_body(message)[:800]}")

    thread = gmail.get_thread(message["threadId"])
    print(f"\n--- 会话重组：{len(thread['messages'])} 封 ---")
    print(f"已升级到人工: {gmail.is_escalated(thread)}")
    print(format_thread(thread, gmail.address)[:1200])

    if args.reply:
        sent = gmail.send_reply(
            message,
            "Thanks, we received your email. A colleague replies within one business day.\n\nHarbor & Pine support",
        )
        print(f"\n已发送回复: {sent['id']} (threadId={sent['threadId']})")

    if args.mark:
        gmail.add_label(message_id, PROCESSED_LABEL)
        print(f"\n已给 {message_id} 打上 {PROCESSED_LABEL}")

    gmail.close()
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
