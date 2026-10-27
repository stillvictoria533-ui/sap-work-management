# 2026-10-27 已上线版本同步 GitHub

状态：已将当前线上代码与本次项目管理规则更新推送到 GitHub `main`，并实时读回远端提交。代码提交：[da2a7ff](https://github.com/stillvictoria533-ui/sap-work-management/commit/da2a7ff91928b73372d1cfee85adfaee67280f6e)。

## 同步范围

- 2026-10-23 已上线的顾问开发日历、数据隔离与工作台独立滚动。
- 2026-10-26 已上线的详情阅读字号调整，以及 AI 执行边界和当前 Mac 后台运行包 `permissions-20261024-ef8bb09dad4c`。
- 对应回归测试、发布记录和预览资料。
- 本次更新的 [项目协作规则](../../../AGENTS.md)、[上线后同步要求](../../OPERATIONS.md#上线后同步-github) 和 [README](../../../README.md)。

本次从独立目录整理上线快照，原开发工作区和 AI 权限改造工作树的修改均保留。未上线的应用连接器及 `assistant/0003–0004` 迁移不进入该快照；仓库原有运维工具和忽略规则保留，不使用服务器旧副本回退它们。密钥、数据库、附件、备份、运行状态与本地项目快照未上传。

## 核对与验证

| 检查 | 结果 |
| --- | --- |
| 服务器应用源码 | 103 份运行源码 SHA256 与同步版本一致 |
| API 运行镜像 | 容器中的 67 份 Python 源码与服务器相应文件一致；根目录 manage.py 单独随部署源码核对 |
| 当前 Mac 后台程序 | 11 份运行包文件 SHA256 一致 |
| 前端构建 | TypeScript 与 Vite 构建通过，28 个应用静态资源与线上 web 容器逐文件一致 |
| 前端单测 | 19 项通过 |
| 后端回归 | SQLite 执行 158 项，157 项通过，1 项 PostgreSQL 并发检查跳过 |
| 脚本单测 | 执行 65 项，63 项通过，2 项环境能力检查跳过 |
| 迁移检查 | No changes detected |
| 提交检查 | 差异格式检查通过，无密钥文件、运行数据或依赖目录 |
| GitHub CI | PostgreSQL 后端、脚本测试、迁移检查、前端单测与构建全部通过，见 [checks](https://github.com/stillvictoria533-ui/sap-work-management/actions/runs/40440584967) |

源码和构建产物摘要、检查结果及远端提交号见 [同步证据](verification.json)。本次仅同步仓库，未重新部署、迁移生产数据库、执行 SAP 查询或发送业务通知。
