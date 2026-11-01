-- 初始表结构，来自课程 Database 页。
-- 列名沿用课程的 gmail_* 命名，这样后面 Dashboard 页的查询可以照抄。

create table events (
  id bigint generated always as identity primary key,
  gmail_message_id text not null unique,
  status text not null default 'processing'
    check (status in ('processing', 'processed', 'skipped', 'failed')),
  attempts int not null default 1,
  error text,
  result jsonb,
  created_at timestamptz not null default now(),
  processed_at timestamptz
);

create table tickets (
  id bigint generated always as identity primary key,
  gmail_thread_id text not null unique,
  customer_email text not null,
  subject text not null default '',
  category text,
  status text not null default 'open'
    check (status in ('open', 'answered', 'escalated')),
  proposed_action text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table messages (
  id bigint generated always as identity primary key,
  ticket_id bigint not null references tickets (id) on delete cascade,
  gmail_message_id text not null unique,
  direction text not null check (direction in ('in', 'out')),
  from_address text not null,
  body text not null,
  created_at timestamptz not null default now()
);

create index messages_ticket_id_idx on messages (ticket_id);

-- 一行一条知识，而不是一段一列：加一个新板块是加数据，不是加迁移。
create table knowledge_base (
  id bigint generated always as identity primary key,
  section text not null,
  title text not null,
  content text not null,
  updated_at timestamptz not null default now(),
  unique (section, title)
);

-- RLS 打开且不建任何 policy，所以 Data API 读不到这些表（后端用 postgres 用户连接，绕过 RLS）。
alter table events enable row level security;
alter table tickets enable row level security;
alter table messages enable row level security;
alter table knowledge_base enable row level security;
