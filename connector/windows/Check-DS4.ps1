$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
. (Join-Path $PSScriptRoot 'Connector.Core.ps1')
try {
    Invoke-SapDeskGuiRead -Offline | ConvertTo-Json -Depth 8
} catch {
    $code = [string]$_.Exception.Message
    if (@('timeout','output_limit','invalid_evidence','gui_unavailable') -notcontains $code) { $code = 'gui_unavailable' }
    @{error_code=$code} | ConvertTo-Json -Compress
    exit 1
}
