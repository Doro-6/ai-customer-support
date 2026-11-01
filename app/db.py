"""Supabase/Postgres 访问层：事件、工单、消息、知识库。"""

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import settings


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database_url, autocommit=True, row_factory=dict_row)


def start_event(gmail_message_id: str) -> dict | None:
    """认领一封信。已处理或正在处理时返回 None。"""
    with connect() as conn:
        return conn.execute(
            """
            insert into events (gmail_message_id) values (%s)
            on conflict (gmail_message_id) do update
              set attempts = events.attempts + 1, status = 'processing', error = null
              where events.status = 'failed'
            returning id, attempts
            """,
            (gmail_message_id,),
        ).fetchone()


def finish_event(event_id: int, status: str, result: dict) -> None:
    with connect() as conn:
        conn.execute(
            "update events set status = %s, result = %s, processed_at = now() where id = %s",
            (status, Jsonb(result), event_id),
        )


def fail_event(event_id: int, error: str) -> None:
    with connect() as conn:
        conn.execute(
            "update events set status = 'failed', error = %s where id = %s",
            (error, event_id),
        )


def upsert_ticket(gmail_thread_id: str, customer_email: str, subject: str) -> int:
    with connect() as conn:
        row = conn.execute(
            """
            insert into tickets (gmail_thread_id, customer_email, subject)
            values (%s, %s, %s)
            on conflict (gmail_thread_id) do update set updated_at = now()
            returning id
            """,
            (gmail_thread_id, customer_email, subject),
        ).fetchone()
        return row["id"]


def update_ticket(
    ticket_id: int,
    status: str,
    category: str | None = None,
    proposed_action: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            update tickets
            set status = %s, category = coalesce(%s, category),
                proposed_action = %s, updated_at = now()
            where id = %s
            """,
            (status, category, proposed_action, ticket_id),
        )


def save_message(
    ticket_id: int,
    gmail_message_id: str,
    direction: str,
    from_address: str,
    body: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            insert into messages (ticket_id, gmail_message_id, direction, from_address, body)
            values (%s, %s, %s, %s, %s)
            on conflict (gmail_message_id) do nothing
            """,
            (ticket_id, gmail_message_id, direction, from_address, body),
        )




def list_sections() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "select distinct section from knowledge_base order by section"
        ).fetchall()
        return [row["section"] for row in rows]


def get_section(section: str) -> list[dict]:
    with connect() as conn:
        return conn.execute(
            "select title, content from knowledge_base where section = %s order by id",
            (section,),
        ).fetchall()




def list_events(limit: int = 100) -> list[dict]:
    with connect() as conn:
        return conn.execute(
            "select * from events order by id desc limit %s", (limit,)
        ).fetchall()


def list_tickets(limit: int = 100) -> list[dict]:
    with connect() as conn:
        return conn.execute(
            "select * from tickets order by updated_at desc limit %s", (limit,)
        ).fetchall()


def list_messages(ticket_id: int) -> list[dict]:
    with connect() as conn:
        return conn.execute(
            "select * from messages where ticket_id = %s order by id", (ticket_id,)
        ).fetchall()


def list_knowledge_base() -> list[dict]:
    with connect() as conn:
        return conn.execute(
            "select * from knowledge_base order by section, id"
        ).fetchall()


def update_knowledge_base_entry(entry_id: int, title: str, content: str) -> dict | None:
    with connect() as conn:
        return conn.execute(
            """
            update knowledge_base set title = %s, content = %s, updated_at = now()
            where id = %s returning *
            """,
            (title, content, entry_id),
        ).fetchone()
