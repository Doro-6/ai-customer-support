# AI 客服邮件助手

一个虚构户外用品店的客服邮箱机器人。它轮询收件箱，用小模型把来信分类，再用大模型通过工具读知识库起草回复，最后由**纯 Python 代码**决定是直接发出还是转给人工。

按 [Datalumina 全栈 AI 工程课程](https://learn.datalumina.com/docs/customer-support-automation) 的思路实现，但有几处刻意的替换——原技术栈在这台机器上根本连不上，详见[与原课程的差异](#与原课程的差异)。

简体中文 | [English](README.en.md)

```
客户来信
    │
    ▼
轮询进程（每 30 秒）
    │
    ▼
护栏 ──────────────► 跳过：自己发的、自动回复、群发邮件
    │
    ▼
分类器（deepseek-flash）──► 必须转人工吗？
    │                            │
    │ 否                         │ 是
    ▼                            ▼
起草 Agent + 知识库工具      回信致谢
（deepseek-v4-pro）               │
    │                            ▼
    ▼                       转人工：打 needs-human 标记，
Python 规则决定             记录建议动作
    │
    ├─► 回复客户
    └─► 转人工
```

**模型只提议，代码做决定。** Agent 无权承诺退款、发货、换货、到货时间或电话回访——这些一律转给同事，`app/ai/rules.py` 会算出下一步该做什么（`ship-part`、`ship-panels`、`new-board`、`valve-check` 等），作为给同事的建议。

## 代码结构

| 路径 | 作用 |
|---|---|
| `app/gmail.py` | IMAP + SMTP 邮箱客户端。名字沿用课程的 `Gmail`，但实现已经和 Gmail 无关。 |
| `app/guards.py` | 四条规则，决定哪些邮件绝对不能回。 |
| `app/pipeline.py` | 一封信的完整处理：护栏、认领、入库、AI 决策、发送、打标签。 |
| `app/poller.py` | 每 30 秒重复一次上面那套动作的常驻进程。 |
| `app/db.py` | Supabase/Postgres 访问层：事件、工单、消息、知识库。 |
| `app/ai/` | 数据结构、分类器、起草 Agent、换货规则、`decide()`。 |
| `app/main.py` | FastAPI：`/health`、带 HTTP Basic 的看板 API、托管前端产物。 |
| `knowledge_base/` | 六个 Markdown 板块，按 `##` 标题拆成一行一条灌进数据库。 |
| `evals/` | 18 条黄金邮件，用 `pydantic-evals` 比对 section 和 action。 |
| `dashboard/` | Vite + React + shadcn 后台，构建进镜像，由 FastAPI 托管。 |
| `scripts/` | 离线自检、邮箱检查、知识库灌入、数据库迁移、部署脚本。 |
| `supabase/migrations/` | 建表 SQL，通过 Supabase CLI 应用。 |

## 快速开始

需要：Python 3.13（uv 会自动下载）、[uv](https://docs.astral.sh/uv/)、Node 22+（用于后台）、以及一个开启了 IMAP 且拿到授权码的邮箱。

```bash
uv sync                                                        # 装依赖
cp .env.example .env                                           # 然后填下面那张表
uv run python -m scripts.mailbox_selftest                      # 27 项离线自检，不需要网络
uv run python -m scripts.db_migrate                            # 应用 supabase/migrations
uv run python -m scripts.seed_knowledge_base                   # 把 knowledge_base/ 灌进数据库
```

两个进程分开跑：

```bash
uv run fastapi dev app/main.py      # API，如果前端构建过就一起托管
uv run python -m app.poller         # 轮询进程
```

后台是独立前端，构建一次即可：`cd dashboard && npm install && npm run build`，
之后 `app/main.py` 会把 `dashboard/dist` 挂在 `/`。

### 环境变量

`.env` 已被 git 忽略，`.env.example` 列出全部键。

| 键 | 含义 |
|---|---|
| `MAIL_ADDRESS`、`MAIL_AUTH_CODE` | 客服邮箱和它的 IMAP/SMTP **授权码**（不是登录密码）。 |
| `IMAP_HOST` / `IMAP_PORT` | 默认 QQ 邮箱（`imap.qq.com:993`）。 |
| `SMTP_HOST` / `SMTP_PORT` | 默认 `smtp.qq.com:465`；填 587 会改用 STARTTLS。 |
| `DATABASE_URL` | Supabase **Session pooler** 连接串。直连地址只有 IPv6，很多网络下连不上。 |
| `DEEPSEEK_API_KEY` | 两个模型都通过 Pydantic AI 调用它。 |
| `CLASSIFIER_MODEL` / `AGENT_MODEL` | 默认 `deepseek:deepseek-flash` 和 `deepseek:deepseek-v4-pro`。 |
| `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` | 看板的 HTTP Basic。密码留空则所有 API 返回 401。 |
| `POLL_INTERVAL_SECONDS` | 开发时 30，真跑时用 600 更合理。 |
| `PIPELINE_DRY_RUN` | 填 `1` 则照常入库但不真发信。 |

## 测试

两层，都不需要凭据（只有 evals 需要 API key）：

```bash
uv run python -m scripts.mailbox_selftest    # 27 项：MIME 解析、护栏、回复主题
uv run python -m evals.run_evals             # 18 条黄金邮件，打真实模型
```

自检覆盖的正是「线上出问题也不报错」的那部分：GB2312 编码正文、只有 HTML 的邮件、引文剥离、四条护栏、回复主题构造。

`evals/run_evals.py` 里 `max_concurrency=1` 不是保守，是必须：Pydantic AI 的 HTTP 客户端绑在创建它的事件循环上，而 `evaluate_sync` 的并发 worker 各跑各的循环，并发会抛
`Event ... is bound to a different event loop`。串行执行让所有调用共用同一个循环。18 条约 5 分钟。

## 部署

详见 [DEPLOY.md](DEPLOY.md)。一句话：一台 Linux 主机、两个容器、除 SSH 外不开任何端口，看板通过 SSH 隧道访问。

```bash
pwsh -File scripts/deploy.ps1 -Server deploy@<host>
```

日常运维、以及同事拿到转人工邮件后该怎么操作，见 [OPERATIONS.md](OPERATIONS.md)。

## 安全设计

- 看板用 HTTP Basic，而且是明文 HTTP。所以它只监听 `127.0.0.1`，只能通过 SSH 隧道访问。`DASHBOARD_PASSWORD` 留空时所有 API 直接 401。
- 数据库用户是 `postgres` 角色，它绕过 RLS。因此这几张表对 Supabase Data API 不可用：RLS 开着且没有策略。
- `.env` 从不进镜像：`.dockerignore` 排除它，Compose 在运行时注入。
- 各种凭据只存在于 `.env`，在工作机和服务器上，从不进 git。

## 与原课程的差异

课程用 Gmail、Anthropic 和 Hostinger。这台机器上 Google 全系不可达、没有 Anthropic key、也没有国际支付手段，于是：

| 课程 | 这里 | 原因 |
|---|---|---|
| Gmail API + OAuth 2.0 | IMAP + SMTP 与授权码 | `console.cloud.google.com` 在当前网络不可达。`app/gmail.py` 保持了课程的公开接口，所以 `pipeline.py`、`evals/`、看板都不用改。 |
| Anthropic Claude | DeepSeek `deepseek-flash` + `deepseek-v4-pro` | 这里能用。两个 Agent 都设了 `thinking=False`：DeepSeek 在思考模式下会拒绝强制的 `tool_choice`，导致结构化输出退化成 `auto`，白白耗尽重试预算。 |
| Hostinger VPS | 阿里云轻量应用服务器 | 国内可付款。 |
| Gmail 标签 | IMAP 文件夹，用 `COPY` 写入 | 语义一致：邮件留在收件箱，另存一份副本携带标签。 |
| Gmail 会话 id | `References` 里的根 `Message-ID` | IMAP 没有会话概念。 |
| `db.start_event(uid)` | 改用 `Message-ID` 作主键 | IMAP 的 UID 在邮箱重建后会复用，会导致新的客户来信被静默丢弃。 |

两个后果值得知道，代码里也有说明：

1. `send_reply` 会把回复的副本追加回收件箱，好让 `format_thread` 把 Agent 自己说过的话也喂回去。**拦住机器人回复这份副本的正是 `guards.skip_reason`**——在这里护栏是承重墙，不是保险丝。
2. QQ 邮箱的 IMAP 会忽略搜索条件（用 `SUBJECT` 搜会返回全部邮件），所以所有按头部的筛选都在抓到头部后于本地完成。

## 已知局限

- 单机、单库。它挂了邮件会堆在邮箱里——不会丢，但也无人回复。
- 没有监控。邮箱授权码失效的表现只是每 30 秒一行 `Poll failed`。
- 看板只有一个账号，无角色、无工单分配。一个人用够了，团队不够。
- Supabase 免费版七天无活动会暂停项目；轮询进程本身就是活动，所以只有主机长期关机才会踩到。

## 致谢

架构与课程内容来自 [Datalumina](https://learn.datalumina.com/docs/customer-support-automation)。
本仓库是基于该课程的独立实现，并做了上述替换。
