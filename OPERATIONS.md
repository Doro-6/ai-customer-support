# 运维与使用手册

这份文档面向两类人：**用邮箱提问的客户**（不需要看这份文档）和**处理转人工邮件的同事**。
最后一节是给部署这台机器的人看的。

## 客户怎么用

发邮件到 **`1843386771@qq.com`**，就这些。不需要注册、不需要装 App、不需要登录。
回复会出现在同一个邮件会话里，用客户自己的语言。

| 客户发的内容 | 结果 |
|---|---|
| 尺寸、装配、气阀检查这类能从知识库回答的问题 | 完整的 AI 回答 |
| 缺配件、破损、退款、投诉、要求打电话 | 一句致谢，然后转人工 |
| 自动回复、退信、群发邮件 | 不回复（护栏拦截） |
| 带附件的邮件 | 直接转人工 |

客户不会被告知对面是 AI——提示词里明确禁止提 AI、提示词、知识库这些词。

## 同事怎么处理转人工的邮件

### 1. 待办队列就是 QQ 邮箱里的 `needs-human` 文件夹

登录 `1843386771@qq.com`，左侧文件夹列表里找到 **`needs-human`**。里面每一封就是一封等人处理的邮件。

### 2. 先看看板，确认系统建议做什么

看板地址在服务器上只监听 `127.0.0.1`，需要先建 SSH 隧道（见下面「访问看板」）。
登录后在 **Tickets** 页选中对应工单，右侧会显示 `Proposed action`：

| 建议值 | 意思 |
|---|---|
| `ship-part` | 寄缺失的配件（气泵、桨、鳍……） |
| `ship-panels` | 寄替换板块（破损不超过两块） |
| `ship-hardware-kit` | 寄五金包 |
| `new-board` | 整块新板（漏气，30 天内到货） |
| `patch-kit` | 补胎套装（漏气，超过 30 天） |
| `exchange` | 换货（发错颜色） |
| `full-replacement` | 整件替换（三块以上破损、框架损坏） |
| `valve-check` | 让客户先做气阀检查（这种情况下 AI 会自己回复，通常不会进队列） |
| `ask-details` | 让客户补订单号（同上） |

**这只是建议**。系统不发货、不退款、不做承诺，执行由人决定。

### 3. 回复客户

在 `needs-human` 文件夹里直接回复那封邮件，正常写就行。

### 4. 把邮件从 `needs-human` 文件夹里删掉

**这一步不能省。**

系统判断「这个会话是否已升级给人工」的依据，就是这封邮件在不在 `needs-human` 文件夹里。
不删的话，客户后续再发信，AI 会一直认为有人在处理，不会自动回复。

删掉之后 AI 会重新接管这个会话。

## 访问看板

看板只监听服务器的 `127.0.0.1:8081`，公网访问不到。在本机建隧道：

```powershell
ssh -i "$env:USERPROFILE\.ssh\id_ed25519" -L 8081:127.0.0.1:8081 deploy@<服务器IP>
```

保持这个窗口开着，浏览器访问 <http://127.0.0.1:8081/>。

用户名和密码在服务器的 `.env` 里（`DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`）。
密码为空时所有 API 都返回 401，这是有意的。

关掉隧道窗口，看板就断开——服务本身不受影响。

## 日常检查

```powershell
# 服务是否活着
ssh -i "$env:USERPROFILE\.ssh\id_ed25519" deploy@<IP> "cd ~/ai-customer-support && docker compose ps"

# 看处理日志（跟着看）
ssh -i "$env:USERPROFILE\.ssh\id_ed25519" deploy@<IP> "cd ~/ai-customer-support && docker compose logs -f poller"

# 手动触发一轮轮询（不用等 30 秒）——也可以在看板上点 "Check inbox now"
```

看板上 **Events** 页要关注的：

- `status = processed`：正常处理完
- `status = skipped`：被护栏拦下，或演练模式
- `status = failed`：处理失败，`Error` 列有原因；`attempts` 到了 3 次会放弃并打标签
- `status = processing` 且很久没变：进程可能在处理中被杀，需要人工确认

## 常见故障

| 现象 | 大概原因 | 处理 |
|---|---|---|
| 客户没收到回复 | 回复进了对方的垃圾邮件；或主题畸形被过滤 | 让对方查垃圾箱；邮件主题不要留空 |
| 日志每 30 秒一行 `Poll failed` | 邮箱授权码失效（被重置或停用） | 更新 `.env` 里的 `MAIL_AUTH_CODE`，重启容器 |
| 日志里 `401` / `AuthenticationError` | DeepSeek key 失效或余额耗尽 | 换 key，更新 `.env`，重启容器 |
| 日志里 `EADDRNOTALLOWED` | Supabase 网络白名单里没有这台服务器的 IP | Supabase 控制台加回服务器 IP |
| 容器 `Up` 但完全不处理邮件 | 多半是上面某一条被吞掉了，看完整日志 | `docker compose logs --tail 100 poller` |
| 看板打不开 | 隧道断了 | 重建隧道 |
| 服务全挂 | 服务器重启后 Docker 没起来 | `systemctl start docker`，容器会自己恢复 |

## 部署和更新

见 [DEPLOY.md](DEPLOY.md)。改完代码更新线上：

```powershell
pwsh -File scripts/deploy.ps1 -Server deploy@<IP>
```

服务器上的 `.env` 不会被覆盖（脚本只在首次部署时写入），所以服务器侧的
`DASHBOARD_PORT` 之类改动可以保留。

## 安全须知

- SSH 只允许密钥登录，`root` 和密码登录都已关闭。密钥是 `~/.ssh/id_ed25519`，**丢了就得用云控制台的「远程连接」进去恢复**。
- 所有密钥只在 `.env` 里，不进 git、不进镜像。
- 数据库只接受白名单 IP 的连接；在本机跑 `scripts/db_migrate` 之类会失败，属正常。
- 定期轮换：邮箱授权码、DeepSeek key、看板密码。换完只改 `.env` 然后 `docker compose up -d`。
