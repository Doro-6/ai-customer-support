# AI customer support bot

An email support agent for a fictional e-commerce store. It polls a mailbox, classifies each
message with a small model, drafts an answer with a larger model that reads a knowledge base
through tools, and then lets **plain Python** decide whether to reply or hand the mail to a human.

Built by following the [Datalumina full-stack AI engineering course](https://learn.datalumina.com/docs/customer-support-automation),
with several deliberate substitutions because the original stack was not reachable from this
machine (see [Deviations](#deviations-from-the-course)).

```
customer email
      |
      v
  poller (every 30 s)
      |
      v
  guards -------------> skip: sent by us, auto-reply, bulk mail
      |
      v
  classifier (deepseek-flash) --> must a human handle it?
      |                                  |
      | no                               | yes
      v                                  v
  agent + KB tools                acknowledgement reply
  (deepseek-v4-pro)                      |
      |                                  v
      v                         escalate: label needs-human,
  Python rules decide           record proposed_action
      |
      +--> reply to customer
      +--> escalate
```

The model proposes, the code decides. The agent may not promise a refund, a shipment, a
replacement, a date or a call. Those always go to a colleague, and `app/ai/rules.py` computes the
next step (`ship-part`, `ship-panels`, `new-board`, `valve-check`, ...) as a suggestion for that
colleague.

## What is in here

| Path | What it is |
|---|---|
| `app/gmail.py` | IMAP + SMTP mailbox client. Despite the name it no longer speaks Gmail. |
| `app/guards.py` | Four rules that decide a message must never be answered. |
| `app/pipeline.py` | One message end to end: guards, claim, store, AI, send, label. |
| `app/poller.py` | The long-running process that repeats that every 30 seconds. |
| `app/db.py` | Supabase/Postgres access: events, tickets, messages, knowledge base. |
| `app/ai/` | Schemas, classifier, drafting agent, replacement rules, `decide()`. |
| `app/main.py` | FastAPI: `/health`, the dashboard API behind HTTP Basic, static dashboard. |
| `knowledge_base/` | Six Markdown sections, seeded into the database one row per `##` heading. |
| `evals/` | 18 golden emails scored on section and action with `pydantic-evals`. |
| `dashboard/` | Vite + React + shadcn admin UI, built into the image and served by FastAPI. |
| `scripts/` | Offline self-test, mailbox check, KB seeder, DB migration helper, deploy scripts. |
| `supabase/migrations/` | The schema, applied through the Supabase CLI. |

## Quickstart

Requirements: Python 3.13 (uv fetches it), [uv](https://docs.astral.sh/uv/), Node 22+ for the
dashboard, and a mailbox that allows IMAP with an app password.

```bash
uv sync                                                        # install dependencies
cp .env.example .env                                           # then fill it in, see below
uv run python -m scripts.mailbox_selftest                      # 27 offline checks, no network
uv run python -m scripts.db_migrate                            # apply supabase/migrations/*.sql
uv run python -m scripts.seed_knowledge_base                   # push knowledge_base/ into the DB
```

Run the two processes in separate terminals:

```bash
uv run fastapi dev app/main.py      # API, and the dashboard if it is built
uv run python -m app.poller         # the poller
```

The dashboard is a separate front end. Build it once with `cd dashboard && npm install && npm run build`;
`app/main.py` then mounts `dashboard/dist` at `/`.

### Environment

`.env` is git-ignored; `.env.example` lists every key.

| Key | Meaning |
|---|---|
| `MAIL_ADDRESS`, `MAIL_AUTH_CODE` | The support mailbox and its IMAP/SMTP app password (not the login password). |
| `IMAP_HOST` / `IMAP_PORT` | Defaults to QQ Mail (`imap.qq.com:993`). |
| `SMTP_HOST` / `SMTP_PORT` | Defaults to `smtp.qq.com:465`; port 587 switches to STARTTLS. |
| `DATABASE_URL` | Supabase **session pooler** URI. The direct host is IPv6-only and often unreachable. |
| `DEEPSEEK_API_KEY` | Used by both models through Pydantic AI. |
| `CLASSIFIER_MODEL` / `AGENT_MODEL` | Default to `deepseek:deepseek-flash` and `deepseek:deepseek-v4-pro`. |
| `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` | HTTP Basic for the dashboard. An empty password disables the API. |
| `POLL_INTERVAL_SECONDS` | 30 while developing, 600 in real use. |
| `PIPELINE_DRY_RUN` | `1` stores everything but never sends a reply. |

## Tests

Two layers, both runnable without credentials (only the evals need an API key):

```bash
uv run python -m scripts.mailbox_selftest    # 27 checks: MIME parsing, guards, reply subjects
uv run python -m evals.run_evals             # 18 golden emails against a live model
```

The self-test covers the parts that fail silently in production: GB2312 bodies, HTML-only mail,
quoted-history stripping, the four guard rules, and reply-subject construction.

`evals/run_evals.py` deliberately runs with `max_concurrency=1`. Pydantic AI's HTTP client binds to
the event loop that created it, and `evaluate_sync`'s parallel workers each start their own loop,
which raises `Event ... is bound to a different event loop`. Serial execution keeps every call on
one loop. 18 cases take roughly five minutes.

## Deployment

See [DEPLOY.md](DEPLOY.md). Short version: a single Linux host, two containers, nothing public
except SSH, dashboard through an SSH tunnel.

```bash
pwsh -File scripts/deploy.ps1 -Server deploy@<host>
```

Day-to-day operation, including what a colleague does with an escalated mail, is in
[OPERATIONS.md](OPERATIONS.md).

## Security model

- The dashboard speaks HTTP Basic over plain HTTP. It therefore listens on `127.0.0.1` only and is
  reached through an SSH tunnel. An empty `DASHBOARD_PASSWORD` makes every API route return 401.
- The database user is the `postgres` role, which bypasses RLS. The Supabase Data API is therefore
  unusable for these tables: RLS is on with no policies.
- `.env` never enters the image: `.dockerignore` excludes it and Compose injects it at runtime.
- Credentials live only in `.env`, on the workstation and on the host, never in git.

## Deviations from the course

The course targets Gmail, Anthropic and a Hostinger VPS. This machine had no route to Google, no
Anthropic key and no international payment method, so:

| Course | Here | Why |
|---|---|---|
| Gmail API + OAuth 2.0 | IMAP + SMTP with an app password | `console.cloud.google.com` is unreachable from this network. `app/gmail.py` keeps the course's public surface, so `pipeline.py`, `evals/` and the dashboard are unchanged. |
| Anthropic Claude | DeepSeek `deepseek-flash` + `deepseek-v4-pro` | Available here. Both agents set `thinking=False`: DeepSeek rejects a forced `tool_choice` while thinking, which degrades structured output to `auto` and burns the retry budget. |
| Hostinger VPS | Alibaba Cloud lightweight instance | Payable locally. |
| Gmail labels | IMAP folders, written with `COPY` | Same semantics: the message stays in the inbox, a copy carries the label. |
| Gmail thread ids | Root `Message-ID` from `References` | IMAP has no threads. |
| `db.start_event(uid)` | Keyed on `Message-ID` | IMAP UIDs are reused after a mailbox rebuild, which would silently drop a new customer email. |

Two consequences worth knowing, both explained in the code:

1. `send_reply` appends a copy of the reply back into the inbox, so `format_thread` shows the agent
   its own earlier answers. `guards.skip_reason` is what stops the bot answering that copy: the
   guard is load-bearing here, not just a safety net.
2. QQ Mail's IMAP ignores search criteria (a `SUBJECT` search returns every message), so every
   header-based filter happens client-side after fetching headers.

## Limitations

- Single host, single database. If it goes down, mail queues in the mailbox: nothing is lost, but
  nothing is answered either.
- No monitoring. A broken mailbox password shows up as one `Poll failed` line every 30 seconds and
  nothing else.
- One dashboard account, no roles, no ticket assignment. Fine for one person, not for a team.
- The Supabase free plan pauses a project after seven days without activity; the poller keeps it
  awake, so this only bites if the host stays off.

## Credits

Architecture and course material: [Datalumina](https://learn.datalumina.com/docs/customer-support-automation).
This repository is an independent implementation of that material with the substitutions above.
