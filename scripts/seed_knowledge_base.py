"""把 knowledge_base/*.md 灌进 Supabase 的 knowledge_base 表，一行一个 ## 小节。

    uv run python -m scripts.seed_knowledge_base

可重复执行：冲突时更新 content，所以改完 Markdown 再跑一次即可。
"""

from pathlib import Path

from app import db


def parse(path: Path) -> list[tuple[str, str, str]]:
    """把一个板块文件拆成 (section, title, content)，每个 ## 标题一行。"""
    entries = []
    for block in path.read_text(encoding="utf-8").split("\n## ")[1:]:
        title, _, content = block.partition("\n")
        entries.append((path.stem, title.strip(), content.strip()))
    return entries


with db.connect() as conn:
    for path in sorted(Path("knowledge_base").glob("*.md")):
        rows = parse(path)
        for section, title, content in rows:
            conn.execute(
                """
                insert into knowledge_base (section, title, content) values (%s, %s, %s)
                on conflict (section, title) do update
                  set content = excluded.content, updated_at = now()
                """,
                (section, title, content),
            )
        print(f"{path.stem}: {len(rows)} entries")
