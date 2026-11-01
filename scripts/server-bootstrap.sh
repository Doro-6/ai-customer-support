#!/usr/bin/env bash
# 在全新的 Ubuntu 22.04 / 24.04 上以 root 运行一次，把机器准备成能跑这套容器的状态。
#
#   bash server-bootstrap.sh                 # 允许任意来源 SSH（不推荐）
#   bash server-bootstrap.sh 1.2.3.4/32      # 只允许这个 IP 连 SSH（推荐）
#
# 设计原则：
# 1. 幂等——重复执行不会坏事；
# 2. 只在确认部署用户已拿到 authorized_keys 之后才关闭 root 登录，避免把自己锁在外面；
# 3. Docker 走阿里云镜像源，国内机器比 get.docker.com 快且稳。
set -euo pipefail

SSH_CIDR="${1:-}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
DEPLOY_HOME="/home/${DEPLOY_USER}"

if [[ ${EUID} -ne 0 ]]; then
  echo "请用 root 运行：sudo bash $0 [允许的SSH来源CIDR]" >&2
  exit 1
fi

echo "==> 1/6 基础包"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg ufw rsync >/dev/null

echo "==> 2/6 Docker（阿里云镜像源）"
if command -v docker >/dev/null 2>&1; then
  echo "    已安装：$(docker --version)"
else
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck disable=SC1091
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://mirrors.aliyun.com/docker-ce/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
  systemctl enable --now docker
  echo "    装好了：$(docker --version)"
fi

echo "==> 3/6 部署用户 ${DEPLOY_USER}"
if id "${DEPLOY_USER}" >/dev/null 2>&1; then
  echo "    已存在"
else
  adduser --disabled-password --gecos "" "${DEPLOY_USER}" >/dev/null
  echo "    已创建"
fi
usermod -aG docker,sudo "${DEPLOY_USER}"
echo "${DEPLOY_USER} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-${DEPLOY_USER}"
chmod 0440 "/etc/sudoers.d/90-${DEPLOY_USER}"

# 云厂商通常把公钥注入到 root 名下，这里复制一份给部署用户，
# 否则关掉 root 登录后你就进不去了。
install -d -m 700 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "${DEPLOY_HOME}/.ssh"
if [[ -s /root/.ssh/authorized_keys ]]; then
  install -m 600 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" \
    /root/.ssh/authorized_keys "${DEPLOY_HOME}/.ssh/authorized_keys"
  echo "    已从 root 复制 authorized_keys"
fi

echo "==> 4/6 防火墙"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
if [[ -n "${SSH_CIDR}" ]]; then
  ufw allow from "${SSH_CIDR}" to any port 22 proto tcp >/dev/null
  echo "    SSH 仅允许 ${SSH_CIDR}"
else
  ufw allow 22/tcp >/dev/null
  echo "    SSH 允许任意来源（建议下次传 CIDR 收紧）"
fi
ufw --force enable >/dev/null
echo "    注意：8080 没有对外开放，看板走 SSH 隧道访问"

echo "==> 5/6 SSH 加固"
KEY_OK=0
if [[ -s "${DEPLOY_HOME}/.ssh/authorized_keys" ]]; then KEY_OK=1; fi
if [[ ${KEY_OK} -eq 1 ]]; then
  sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
  sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  sshd -t && systemctl reload ssh 2>/dev/null || systemctl reload sshd
  echo "    已关闭 root 登录和密码登录"
else
  echo "    跳过加固：部署用户还没有 authorized_keys，先关会把你锁在外面"
fi

echo "==> 6/6 完成"
echo
echo "下一步（在你自己电脑上执行，不是服务器）："
echo "  powershell -File scripts/deploy.ps1 -Server ${DEPLOY_USER}@<服务器IP>"
echo
echo "如果 docker pull 很慢，给 /etc/docker/daemon.json 配镜像加速后 systemctl restart docker"
