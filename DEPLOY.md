# 上线手册

目标形态和课程一致：**一台常开的 Linux 机器跑着两个容器（api + poller），除了 SSH 之外不对公网开放，看板通过 SSH 隧道访问。**

之所以不开放看板端口：看板用的是 HTTP Basic 认证，明文传输。挂在公网上等于把用户名密码明文广播。数据库和邮箱凭据都在同一台机器上，一旦被拿下损失是连锁的。

> 已经在阿里云轻量上跑通一次，实测记录见文末「踩坑记录」。那台机器是 **Alibaba Cloud Linux 4**（RHEL 系），和下面假设的 Ubuntu 有差异。

## 你需要先做的一件事：买一台 VPS

| 项目 | 建议 |
|---|---|
| 配置 | 1–2 vCPU、**2 GB 内存**、20–40 GB 盘。够用，构建时有 2GB 不会 OOM |
| 系统 | **Ubuntu 22.04 / 24.04** 最省事；阿里云镜像默认的 Alibaba Cloud Linux 4 也能跑，但要额外几步（见踩坑记录） |
| 地域 | 国内（阿里云/腾讯云轻量应用服务器）最省事：支付宝付款、不需要国际卡、网络不用翻墙 |
| 价格参考 | 轻量应用服务器入门档通常 ¥60–120/年 |
| 创建时 | 会让你填 SSH 公钥，粘贴下面这一行 |

本机已生成的公钥（`~/.ssh/id_ed25519.pub`）：

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG/vxFu4nqwPT3Z69foJ+61Ai10Ooge+50SuYNwxLcTI deploy@ai-customer-support
```

> 如果服务商只支持密码登录，那也没问题——第一次用密码登录后把公钥贴进 `~/.ssh/authorized_keys` 即可，后面 bootstrap 脚本会帮你加固。

## 部署分三步

### 第 1 步：准备服务器（一次性）

把本仓库的 `scripts/server-bootstrap.sh` 传到服务器并执行。它会：

1. 装 Docker（走阿里云镜像源，国内快）
2. 建 `deploy` 用户并加入 `docker` / `sudo` 组
3. 把 root 的 `authorized_keys` 复制给 `deploy`（否则关掉 root 登录会把自己锁死）
4. 防火墙：默认拒绝入站，**只放行你指定 IP 的 SSH**
5. 关闭 root 登录和密码登录（**仅在确认 deploy 用户已有公钥时**）

```bash
# 在你自己的电脑上
scp -i ~/.ssh/id_ed25519 scripts/server-bootstrap.sh root@<服务器IP>:/root/

# SSH 登录服务器后
sudo bash /root/server-bootstrap.sh <你家的公网IP>/32
```

`<你家的公网IP>/32` 很关键——只让这一个地址能连 SSH。可以访问 `ip.sb` 或用 `(Invoke-RestMethod ifconfig.me)` 查。

### 第 2 步：部署应用

回到本机，一条命令：

```powershell
powershell -File scripts\deploy.ps1 -Server deploy@<服务器IP>
```

它做四件事：打包（0.2 MB，排除 `.venv`/`node_modules`/`.git`/`dist`）→ `scp` 上传 → 远端解包 → `docker compose up -d --build` + 健康检查。

### 第 3 步：访问看板

```powershell
ssh -i "$env:USERPROFILE\.ssh\id_ed25519" -L 8080:localhost:8080 deploy@<服务器IP>
```

保持这个窗口开着，浏览器访问 **http://127.0.0.1:8080/** ，账号 `admin` / 密码见 `.env` 的 `DASHBOARD_PASSWORD`。

## 上线后的运维

| 要做的事 | 命令 |
|---|---|
| 看轮询日志 | `ssh deploy@<ip> 'cd ~/ai-customer-support && docker compose logs -f poller'` |
| 改代码后发版 | 再跑一次 `deploy.ps1`（会重建镜像） |
| 重启服务 | `ssh deploy@<ip> 'cd ~/ai-customer-support && docker compose restart'` |
| 看资源占用 | `ssh deploy@<ip> 'docker stats --no-stream'` |

容器带了 `restart: unless-stopped`，所以服务器重启后会自动恢复，不需要开机脚本。

## 三件必须知道的限制

**1. Supabase 免费版 7 天无活动会暂停**

poller 每 30 秒查一次库，本身就构成活动，所以正常运行时不会暂停。但如果机器长时间关机，第 8 天回来数据库是暂停状态，需要去控制台手动恢复。

**2. 单机没有高可用**

这台机器挂了，收邮件就停了。客户邮件不会丢（还在 QQ 邮箱里），但会积压。对一个小客服机器人来说这是可接受的取舍。

**3. 密钥都在这台机器上**

`.env` 里有数据库连接串、DeepSeek key、QQ 授权码。所以：SSH 只放行你的 IP、关闭密码登录、定期轮转授权码。`.env` 不会被 `docker compose` 打进镜像（`.dockerignore` 里有），是运行时挂进去的。

## 如果你不想买服务器

那就不算"上线"，只能算"本机跑通"。替代方案都有明显代价：

| 方案 | 代价 |
|---|---|
| 家里旧电脑/NAS 长期开机 | 家宽多数没有公网 IP，要内网穿透；功耗和噪音 |
| 内网穿透（frp 等） | 仍然需要一台有公网 IP 的机器做服务端 |
| Cloudflare Tunnel | 国内访问不稳定，需要域名托管在 CF |
| 继续本机跑 | 电脑睡眠就停，等于没有服务 |

## 国内服务器上 docker pull 慢的话

给 `/etc/docker/daemon.json` 加镜像加速（各云厂商控制台都有专属地址）：

```json
{
  "registry-mirrors": ["https://<你的专属加速地址>.mirror.aliyuncs.com"]
}
```

然后 `sudo systemctl restart docker`。

本项目要拉的镜像只有三个：`python:3.13-slim`、`node:22-slim`、`ghcr.io/astral-sh/uv`。如果 `ghcr.io` 在服务器上不通，把 Dockerfile 第一行改成用 pip 装 uv：

```dockerfile
RUN pip install --no-cache-dir uv
```

（这一点我已在本地验证过 ghcr 可达，但国内服务器情况可能不同。）

---

## 踩坑记录（2026-10-31，阿里云轻量 · Alibaba Cloud Linux 4）

第一次上线实际遇到的七个问题，按遇到的顺序：

**1. 镜像不是 Ubuntu。** 阿里云默认给的是 `Alibaba Cloud Linux 4.0.4 (Agentic Edition)`，RHEL 系：`dnf` 而非 `apt`，没有 `ufw`，`firewalld` 未启用。`scripts/server-bootstrap.sh` 的 Debian 分支在这台机器上用不了，改为手工执行等价步骤。

**2. Docker Hub 连不上。** `registry-1.docker.io` 超时，但 `ghcr.io` 通、pypi.org 通。解法是给 `/etc/docker/daemon.json` 加镜像加速：

```json
{ "registry-mirrors": ["https://docker.m.daocloud.io", "https://docker.1panel.live"] }
```

**3. 缺 compose 插件，且 docker-ce 仓库是坏的。** 自带 `docker-ce.repo` 指向 `download.docker.com/linux/centos/4`（把 releasever 解析成 4），返回 404。`docker-compose-plugin` 是静态二进制、不挑发行版，直接从阿里云镜像下 RPM 装：

```bash
curl -fsSL -o /tmp/cp.rpm "https://mirrors.aliyun.com/docker-ce/linux/centos/9/x86_64/stable/Packages/docker-compose-plugin-5.6.1-1.el9.x86_64.rpm"
rpm -Uvh --nodeps /tmp/cp.rpm
```

**4. 8080 被预装服务占用。** 该镜像自带一个 `searxng` 容器，占了 `127.0.0.1:8080`。没动它，改用 `DASHBOARD_PORT=8081`（compose 里已参数化）。

**5. `useradd` 创建的账号是锁定的。** `useradd -m deploy` 之后密码字段是 `!`，sshd 直接拒绝：`User deploy not allowed because account is locked`，**即使密钥正确也登不上**。解法是设一个随机密码解锁（之后 sshd 仍关闭密码登录，不构成风险）：

```bash
echo "deploy:$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 32)" | chpasswd
```

**6. Windows PowerShell 5.1 读不了 UTF-8 无 BOM 的脚本。** 带中文的 `.ps1` 被当成 ANSI 解析，中文乱码顺带破坏引号配对，报一堆 `Missing expression after unary operator`。`deploy.ps1` 现在改为**全 ASCII**，并且用 `pwsh`（7.x）运行；`powershell`（5.1）还需要 `-ExecutionPolicy Bypass`。

**7. `ssh-keygen -N ""` 的引号陷阱。** 在 PowerShell 里写 `-N '""'` 会把两个引号当字面量传进去，生成一把**带口令 `""` 的私钥**，之后所有免交互登录都会失败并报 `Private key file is encrypted`。正确写法是 `-N ""`（空字符串）。

### 上线后的访问方式

看板只在服务器 `127.0.0.1:8081` 上监听，本机建隧道：

```powershell
ssh -i "$env:USERPROFILE\.ssh\id_ed25519" -L 8081:127.0.0.1:8081 deploy@<IP>
```

然后浏览器开 <http://127.0.0.1:8081/> 。隧道关掉，看板就断开——这是有意的。

### 安全状态

- SSH：仅密钥登录，root 已禁用，密码登录已禁用
- 阿里云安全组：只放行 22
- 应用端口：仅监听 loopback，公网不可达
- 看板：HTTP Basic（隧道内明文可接受）
