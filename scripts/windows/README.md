# Windows 内网连接机：首次环境检查

适用目标：用户提供的 Windows 10 电脑，有安装权限，并在本机 SAP GUI 直接登录 SAP。

这是环境检查脚本，不是 SAP 连接程序或安装包。当前阶段不安装软件、不注册设备、不读取 SAP 数据或账号密码，不修改防火墙、休眠、服务、证书信任或执行策略。只访问 `43.130.36.105:443` 和 `/api/health`。

## 运行

1. 将 `Check-SapDeskWindows.ps1` 复制到目标 Windows 电脑。
2. 打开普通 Windows PowerShell，在脚本所在目录运行：

```powershell
.\Check-SapDeskWindows.ps1
```

检查不要求管理员提权。TCP 检查可能需要几十秒。若公司策略禁止运行脚本，交给 IT 审阅执行，不要修改全局执行策略或关闭证书校验。

3. 将 `SAP DESK CHECK RESULT` 和 `END OF RESULT` 之间的 JSON 结果发回。报告只包含操作系统/运行库状态、网络源地址和连通性结果，不包含电脑名、登录用户名、许可证序列号或密码；报告不会自动上传。

## 如何理解

- `SystemType` 区分 x64/ARM 等机器类型；`OSArchitecture` 是操作系统位数，`PowerShellProcess64Bit` 是当前 PowerShell 进程位数，两者不要混淆。
- `CloudDirectTcp443 = true` 表示直接 TCP 连接成功，不等于完成设备注册或 SAP 查询。
- `CloudHttpsHealth = ok` 表示健康接口经正常证书校验后返回正确结果，不等于未来服务账号的代理、凭据和连接方式也已验证。
- TCP 失败而 HTTPS 成功时，可能存在代理访问路径，需要进一步核对；不要直接认定所有网络访问均被阻止。
- Node 或 SDK 标记为 false 不影响完成这次检查，后续再选择、安装匹配的运行环境。
- DLL 文件存在只是安装线索，没有加载 DLL 或验证版本/架构兼容性。
- 三个 SAP 系统的接口连通性、只读账号权限和数据范围仍待独立验证。本脚本不猜测 RFC/ADT 端口，也不连接 PS4。

## 当前验证边界

脚本基于 Windows PowerShell 5.1 的标准命令，已作静态检查，尚未在用户这台 Windows 电脑执行。环境检查通过后仍需开发连接服务及配对接口，并完成 DS4/110、QS4/120、PS4/100 的分系统只读联调。

参考：[Microsoft Test-NetConnection](https://learn.microsoft.com/powershell/module/nettcpip/test-netconnection)、[Win32_OperatingSystem](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-operatingsystem)、[SAP RFC SDK](https://support.sap.com/en/product/connectors/nwrfcsdk.html)。
