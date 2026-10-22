@echo off
setlocal
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -STA -File "%~dp0Start-Connector.ps1"
echo.
echo If Windows blocked the script, ask IT to review/authorize it. Do not disable policy.
pause
