# SAP Desk Windows 免安装连接器 · 待实机验证

面向只有 SAP GUI、没有 Node.js/工具箱/RFC SDK 的 Windows 电脑。
使用 Windows 自带的 Windows PowerShell 5.1 与 SAP GUI for Windows 的 Scripting 接口。
不安装服务，不添加开机项，不启用脚本策略，不修改 SAP GUI/服务器配置，不要求安装 ChatGPT。

**当前范围与 Mac 不同：Windows 仅支持读取已登录 DS4/110 会话的事务码、程序和屏幕号。**
不能自动打开 SE38、查询任意事务码映射、读 DDIC、导出画面/业务记录或执行 SAP 修改。
服务器按设备能力约束查询；不要把“Windows 原型已编写”当成“Windows 实机已通过”。

## 第一步：先在 Windows 本机检测（无需配对，不会上传）

1. 将完整压缩包解压到本机文件夹，不要在压缩包内直接运行。
2. 用同一个 Windows 用户和桌面会话正常运行本包（无需以管理员身份运行）。在 SAP GUI 中登录 **DS4 / 110**。只保留一个 DS4/110 会话，停在一个稳定页面。
3. 双击 **Check-DS4.cmd**。
4. 如 SAP GUI 出现脚本访问通知，在你确认这是本次检测后按正常流程处理；程序不会关闭此提示。
5. 将控制台结果发回来。成功时仅显示 `transaction`、`program`、`screen` 和证据标记，不输出 SAP 用户名、密码、服务器名或画面文本。

此检测只读本地 SAP GUI 的会话元数据，最多 60 秒后停止读取子进程，不会关闭 SAP GUI。
它会检查 DS4/110、系统编号 00，以及应用服务器解析到 10.2.22.34；负载均衡未提供应用服务器时拒绝，不会自动放宽校验。

常见结果：

| 结果 | 含义与处理 |
| --- | --- |
| result | 已读到当前会话元数据，仍需后续网页配对测试 |
| gui_unavailable | 未运行/登录 SAP GUI、未安装脚本组件、脚本开关被禁用或 COM 访问失败；需进一步定位，不代表必须重装 |
| gui_session_missing | 没有可读取的 DS4/110 登录会话，或当前会话忙碌 |
| gui_session_ambiguous | 多个 DS4/110 会话，需由你选择并保留一个测试会话 |
| gui_endpoint_unverified | 当前连接的服务器/系统号未匹配固定 DS4 ；请确认实际连接，可能涉及负载均衡 |
| gui_busy | 页面正在通信或读取中发生变化，停稳后重试 |
| timeout | SAP GUI 自动化调用未在时限内返回，读取子进程已终止 |

如果提示脚本执行被组织策略阻止：请 IT 审核/签名或允许这个具体脚本。启动器**不使用 ExecutionPolicy Bypass**，不要关闭全局脚本策略或系统防护。
如果 64 位 PowerShell 无法访问 SAP GUI，而 SAP GUI 是 32 位，可由维护人员核对匹配的运行位数后再测试；本包尚未验证这种组合。

## 第二步：配对并回传（接口发布或具备可信 HTTPS 测试地址后）

1. 从 SAP Desk AI 助手的「管理连接与查询」生成配对码。
2. 双击 **Start-Connector.cmd**，输入经过确认的 SAP Desk HTTPS 地址和一次性配对码。
3. 网页选择此 Windows 设备，可选操作应只有「当前 SAP GUI 会话」，对象固定 CURRENT。
4. 提交查询后，终端显示接收服务器和范围；按 **Y** 允许读取并回传，按 **N** 拒绝。
5. 结果先回到自己的 SAP Desk 账号。主动附加到 AI 提问后才会发送给配置的外部模型；本次没有授权上传任何 Windows SAP 数据。
6. 网页「断开」撤销授权；或者 Ctrl+C 停止连接器。SAP GUI 不受关闭影响。

**当前线上服务器尚未发布连接器接口。Mac 的 127.0.0.1 地址也不能从 Windows 访问。**
本机预览只允许同机回环 HTTP；跨机器测试需要可信 HTTPS 地址。请先完成第一步检测，再安排 HTTPS 测试入口或经确认发布。
不要用忽略证书、明文局域网 HTTP、开放 SAP 端口等方式绕过连接要求。

## 校验与边界

- 同一个配对协议、账号隔离和撤销机制；凭据只在内存；不保存登录密码。
- 设备固定声明 sap-gui-windows，仅接受 gui_session / CURRENT，不接受远程脚本、路径、系统、任意参数或 GUI 控件选择器。
- 只访问会话信息属性；不会发送键盘操作、调用事务码、写文本框、按按钮或导出控件树。
- 结果不含 SAP 用户名；只有 program/screen/source/transaction 和固定证据标记，服务端验证字段及校验值。
- GUI 元数据是本地会话快照，不是服务端 RFC 双重身份验证，不证明已读取 DDIC、ABAP 源码或最新业务状态。
- 准备阶段完成了网页/协议及静态边界检查，Windows PowerShell 和 SAP GUI COM 尚待 Windows 实机验证。
