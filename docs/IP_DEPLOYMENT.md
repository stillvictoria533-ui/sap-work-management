# 用公网 IP 部署 SAP Desk

目标地址：`https://43.130.36.105`。无需购买域名。你在腾讯云终端执行操作，本文件中的命令不会由 Codex 自动在服务器运行。

## 1. 先做只读预检

在腾讯云终端执行下列命令，将输出发回以确认 Debian 版本、Docker 状态和端口占用。它们不安装软件、不停止服务，也不会读取密码。

```sh
cat /etc/os-release
uname -m
id
free -h
df -h /
command -v docker && docker --version
docker compose version
sudo ss -lntp '( sport = :80 or sport = :443 )'
```

若已是 root，可去掉 `sudo`。尚未安装 Docker 时，相关命令报“未找到”属于预期。先确认现有服务，不要为了腾出端口而停止未知进程。

需要 Docker Engine、Compose 插件、Python 3、OpenSSL 1.1.1+ 和系统 CA 根证书。Docker 的 Debian 安装方式见[官方文档](https://docs.docker.com/engine/install/debian/)，具体安装命令按预检结果确定。腾讯云防火墙和系统防火墙需放行 TCP 80/443，保留 SSH 接入；API 8000 和 PostgreSQL 5432 不对公网开放。

## 2. 上传与解压部署包

本地生成部署包：

```sh
python3 scripts/package_release.py
```

输出 `release/sap-desk-ip-deploy.tar.gz`、对应 SHA256 校验文件和文件清单。包内没有开发数据库、本地账号文件、`.env.production`、已上传附件、ACME 账户材料或私钥。

将压缩包及 `.sha256` 文件上传至服务器 `/tmp/`。可以使用你已有的文件传输工具，或在自己的电脑上使用 SCP，用户名按实际 SSH 账号填写：

```sh
scp release/sap-desk-ip-deploy.tar.gz release/sap-desk-ip-deploy.tar.gz.sha256 你的SSH用户名@43.130.36.105:/tmp/
```

在服务器验证并解压。**如果 `/opt/sapdesk` 已存在，先确认其内容，不能直接覆盖已有服务。**

```sh
cd /tmp
sha256sum -c sap-desk-ip-deploy.tar.gz.sha256
sudo tar -xzf sap-desk-ip-deploy.tar.gz -C /opt
cd /opt/sapdesk
sudo sh scripts/server_preflight.sh
```

部署包将代码文件的归属固定为 root，防止普通用户修改由 root 执行的续期脚本。下面命令在 root 终端执行，或按需添加 `sudo`。

## 3. 创建配置

把邮箱替换为你自己的联系邮箱。它用于 ACME 账户，不是默认的管理员登录邮箱，也不等同于到期监控服务。

```sh
cd /opt/sapdesk
python3 scripts/prepare_production_env.py 43.130.36.105 --email 你的联系邮箱
```

脚本会生成权限为 `0600` 的 `.env.production`，生成互不相同的应用/数据库密钥，并自动设置：

```ini
DJANGO_DEBUG=false
APP_DOMAIN=43.130.36.105
DJANGO_ALLOWED_HOSTS=43.130.36.105
CSRF_TRUSTED_ORIGINS=https://43.130.36.105
PUBLIC_BASE_URL=https://43.130.36.105
TRUST_PROXY=true
```

`APP_DOMAIN` 为兼容已有配置保留该名称，可填写公网 IPv4 或域名。脚本拒绝覆盖已有 `.env.production`，不要删除该文件来“重新生成配置”，否则会改变密钥和数据库密码。钉钉默认关闭，后续按 README 配置两种通知通道。

## 4. 验证公网可达并申请证书

Certbot 固定使用官方 `v5.8.0` 容器；公网 IP 使用 `shortlived` 证书配置。首次申请前阅读 [Let's Encrypt Subscriber Agreement](https://letsencrypt.org/repository/)，同意后才添加 `--agree-tos`。

先进行测试签发，确认公网 80 端口可达：

```sh
python3 scripts/certificates.py issue --staging --agree-tos
```

测试成功后再申请正式可信证书：

```sh
python3 scripts/certificates.py issue --agree-tos
```

首次没有正式 Nginx 时，脚本会启动独立的 `sapdesk-certificates` 临时服务，仅提供 `/.well-known/acme-challenge/`，不开放登录、工单或 API。无论签发成功或失败都会尝试停止自己的临时服务，不停止其他应用。

测试证书存放于 `deploy/acme-staging`；生产证书与 ACME 账户存放于 `deploy/letsencrypt`。测试证书不会部署到正式站点。脚本会验证 IP SAN、有效期、系统信任链和私钥匹配，再将成对文件写入 `deploy/certs/releases/`，原子切换 `current` 链接。

这一步暂不启动业务服务。如果签发失败，先检查域名/IP 配置、服务器出网、80 端口与防火墙，不要反复强制签发或关闭证书验证。

## 5. 启动数据库与应用

```sh
docker compose --env-file .env.production build
docker compose --env-file .env.production up -d db --wait
docker compose --env-file .env.production run --rm api python manage.py migrate
docker compose --env-file .env.production run --rm api python manage.py bootstrap_admin --email 你的管理员邮箱 --name 管理员
docker compose --env-file .env.production run --rm api python manage.py check --deploy
docker compose --env-file .env.production up -d
docker compose --env-file .env.production ps
python3 scripts/certificates.py check --live
```

管理员密码在终端交互输入，不放入命令行、Git 或聊天。`check --live` 必须验证文件证书以及 Nginx 实际提供的证书一致，且 TLS 信任与 IP 校验通过。

然后使用浏览器访问 `https://43.130.36.105`，验证登录、顾问创建、附件下载、状态变化、导出和站内提醒。HTTP 访问会重定向至 HTTPS；请收藏 HTTPS 地址。

## 6. 启用自动续期

IP 证书有效期约 6 天，自动续期是上线必做项。先在线上验证目录测试续期，测试不会安装临时证书：

```sh
python3 scripts/certificates.py renew --dry-run
python3 scripts/certificates.py renew
```

通过后安装定时单元（路径按本指南固定为 `/opt/sapdesk`）：

```sh
install -m 644 deploy/sapdesk-cert-renew.service /etc/systemd/system/sapdesk-cert-renew.service
install -m 644 deploy/sapdesk-cert-renew.timer /etc/systemd/system/sapdesk-cert-renew.timer
systemctl daemon-reload
systemctl enable --now sapdesk-cert-renew.timer
systemctl start sapdesk-cert-renew.service
systemctl status sapdesk-cert-renew.service --no-pager
systemctl list-timers sapdesk-cert-renew.timer --no-pager
```

定时任务每天检查 4 次，并添加最多 15 分钟随机延迟。Certbot 只在需要时续期，正常续期通过正在运行的 Nginx 验证目录完成，不停服。

脚本在续期后执行证书校验、`nginx -t`、平滑重载及实际 TLS 校验。切换失败恢复旧证书链接，并返回非零状态。即使上次已签发但安装失败，下次也会重试安装。Certbot 容器不挂载 Docker socket，重载由主机脚本执行。

检查状态：

```sh
journalctl -u sapdesk-cert-renew.service -n 60 --no-pager
python3 scripts/certificates.py check --live
```

应为 systemd 任务失败和证书临期配置运维告警；本项目不会自动发送证书告警邮件。检查脚本对剩余有效期不足一天的证书报错。

## 7. 钉钉与后续运维

- 管理员群机器人和企业内部应用按 README 配置；顾问绑定本组织的钉钉用户 ID。
- 钉钉链接使用 `https://43.130.36.105/#/tickets/工单编号`；登录鉴权照常执行。在钉钉客户端实际点击验证后再启用通知。
- 修改密钥配置后用 `docker compose --env-file .env.production up -d --force-recreate api worker` 让新环境生效。
- 数据备份继续使用 `sh scripts/backup.sh`。另外加密保管 `.env.production`、`deploy/letsencrypt` 和 `deploy/certs`，不要打入源码部署包。
- 公网 IP 更换时，需要更新访问配置并为新 IP 签发证书。不要复用旧 IP 的证书；先安排维护窗口，再处理旧入口与新地址。
- Compose 项目名固定为 `sapdesk`，证书临时服务使用 `sapdesk-certificates`；移动源码目录不会意外切换到另一组命名数据卷。

## 当前验证边界

本地测试使用临时 CA 与真实 OpenSSL 验证证书校验、私钥权限、原子切换和失败回滚；Docker 编排与 ACME 调用通过测试替身验证。不等同于已获得公网 IP 证书或已在腾讯云运行。正式签发、端口可达、容器健康、PostgreSQL 和钉钉客户端访问，均按上述步骤在服务器验收。

参考：[IP 证书与 Certbot](https://letsencrypt.org/2026/03/11/shorter-certs-certbot.html)、[Certbot 续期说明](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates)。
