@echo off
setlocal
cd /d "%~dp0"
echo This reads only DS4 current GUI session metadata. No upload. No SAP changes.
echo Keep one logged-in DS4/110 session open. SAP scripting may display a notice.
echo The check has a 60-second limit. SAP GUI remains open.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -STA -File "%~dp0Check-DS4.ps1"
echo.
pause
