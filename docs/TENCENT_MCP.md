# SAP Desk 腾讯云 MCP

本项目部署在腾讯云 Lighthouse 轻量应用服务器：

- 地域：`na-siliconvalley`
- 实例：`lhins-9wkf7wc5`
- 公网 IP：`43.130.36.105`
- 应用目录：`/opt/sapdesk`，Compose 项目：`sapdesk`

数据库使用服务器内的 PostgreSQL 容器，附件和备份保存在本机。当前接入选用腾讯云官方 `lighthouse-mcp-server@1.0.0`，包含 Lighthouse 查询和 TAT 远程命令能力。

## 本机配置

Codex 服务名：`tencent-lighthouse`。配置已添加到 `/Users/apple/.codex/config.toml`，原配置已有独立备份。运行文件和依赖安装在 `/Users/apple/.codex/mcp/tencent-lighthouse/`，不进入项目发布包。

已启用 12 个工具：实例、地域、磁盘、流量包、防火墙、Docker 状态与活动查询，监控、自检、远程命令及命令任务查询。工具参数中的 `Region` 应使用 `na-siliconvalley`，目标实例使用 `lhins-9wkf7wc5`。配置本身不限制腾讯云凭据的账号权限。

## 完成认证

在本机 Terminal 运行：

```sh
/Users/apple/.codex/mcp/tencent-lighthouse/configure.command
```

依次输入 SecretId、SecretKey；长期密钥的临时 Token 留空。输入不回显，凭据仅保存在同目录 `credentials.json`，权限为 `0600`，目录权限为 `0700`。启动脚本读取凭据，避免依赖桌面应用是否继承终端环境变量。

凭据从[腾讯云 API 密钥管理](https://console.cloud.tencent.com/cam/capi)获取。Lighthouse 查询需要对应查询权限；执行服务器命令还需要 TAT 权限。不要复制 WorkBuddy 内部登录令牌来替代腾讯云 API 凭据，也不要将凭据添加到代码仓库或聊天。

输入完成后在 Codex 的 MCP 设置中重新连接该服务，或重启 Codex。随后通过实例 ID 和地域执行只读实例查询，确认真实账号认证和目标实例匹配。部署、服务重启及防火墙修改按具体任务授权执行。

## 验证记录

2026-10-13 09:39（北京时间）：安装、配置解析、通过配置入口启动 MCP、初始化握手和全部 12 个已启用工具的存在性验证通过。本机凭据已保存，权限 `0600`。真实云 API 认证验证通过：`describe_instances` 查询匹配 `lhins-9wkf7wc5`、`43.130.36.105`，实例状态为 `RUNNING`。

TAT 远程命令验证也通过：仅打印连接标记、查询操作系统以及检查 `/opt/sapdesk` 目录是否存在，返回 `SAPDESK_MCP_CONNECTION_OK`、`Linux`、`SAPDESK_PROJECT_DIRECTORY_OK`。未部署、重启服务、修改防火墙或读取生产密钥。Codex 当前聊天若尚未加载新工具，需要在 MCP 设置中重新连接服务，或重启 Codex。

用户重启 Codex 后，当前聊天已加载 `mcp__tencent_lighthouse` 原生工具。直接通过该工具查询目标实例成功，状态 `RUNNING`；只读远程命令返回 `CODEX_MCP_READY`、`Linux`、`SAPDESK_DIRECTORY_OK`，Docker 服务状态 `active`。重启后的实际使用验证通过。

固定版本实际工具列表中没有 `upload_folder`，文件上传不能据此视为已接通。

参考：[腾讯云 Lighthouse MCP](https://tcb.cloud.tencent.com/mcp-server/mcp-lighthouse)、[OpenAI 官方 MCP 配置文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。
