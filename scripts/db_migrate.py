"""把 supabase/migrations 里的 SQL 应用到数据库，不需要 Supabase CLI。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.config import settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
TABLES = ("events", "tickets", "messages", "knowledge_base")


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database_url, row_factory=dict_row)


def existing_tables(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "select tablename from pg_tables where schemaname = 'public'"
    ).fetchall()
    return {row["tablename"] for row in rows}


def missing() -> list[str]:
    with connect() as conn:
        have = existing_tables(conn)
    return [table for table in TABLES if table not in have]


def status() -> int:
    if not settings.database_url:
        print("DATABASE_URL 是空的。把它填进 .env 再跑这个脚本。")
        return 1
    try:
        with connect() as conn:
            version = conn.execute("select version()").fetchone()["version"]
            have = existing_tables(conn)
    except psycopg.OperationalError as error:
        print(f"连不上数据库：{error}")
        print("检查 DATABASE_URL 是否是 Session pooler 的连接串（端口 5432，域名含 pooler）。")
        return 1

    print(f"已连接：{version.split(',')[0]}")
    print(f"public 下的表（{len(have)} 张）：{', '.join(sorted(have)) or '（空）'}")
    lacking = [table for table in TABLES if table not in have]
    print(f"缺少的表：{', '.join(lacking) if lacking else '无，迁移已完成'}")
    return 0


def migrate() -> int:
    lacking = missing()
    if not lacking:
        print("四张表都在，无需迁移。")
        return 0
    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        print(f"在 {MIGRATIONS} 里没找到 .sql 文件。")
        return 1

    for path in files:
        sql = path.read_text(encoding="utf-8")
        print(f"执行 {path.name} ...")
        with connect() as conn:
            try:
                conn.execute(sql)
            except psycopg.Error as error:
                print(f"  失败：{error}")
                return 1
        print("  完成")

    still = missing()
    if still:
        print(f"迁移后仍缺：{still}")
        return 1
    print("迁移完成，四张表已就绪。")
    return 0


def check() -> int:
    with connect() as conn:
        for table in TABLES:
            row = conn.execute(f"select count(*) as n from {table}").fetchone()
            print(f"{table:<16} {row['n']} 行")
        print("\n最近的 events：")
        for row in conn.execute(
            "select id, gmail_message_id, status, attempts, created_at "
            "from events order by id desc limit 5"
        ).fetchall():
            print(
                f"  #{row['id']} {row['gmail_message_id']:<6} {row['status']:<10} "
                f"attempts={row['attempts']} {row['created_at']:%Y-%m-%d %H:%M:%S}"
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Supabase 迁移与体检")
    parser.add_argument("--status", action="store_true", help="只体检，不修改")
    parser.add_argument("--check", action="store_true", help="看各表行数与最近事件")
    args = parser.parse_args()
    if args.status:
        return status()
    if args.check:
        return check()
    return migrate()


if __name__ == "__main__":
    sys.exit(main())
