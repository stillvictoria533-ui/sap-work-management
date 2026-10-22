# Windows PowerShell 5.1, STA. Local read-only metadata probe; no network upload.
# Never sends keys, navigates transactions, reads screen controls, or modifies SAP.
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function Read-Ds4GuiSnapshot {
    if ($env:OS -ne 'Windows_NT') { throw 'gui_unavailable' }
    try {
        $sap = [Runtime.InteropServices.Marshal]::BindToMoniker('SAPGUI')
        $application = $sap.GetScriptingEngine
    } catch { throw 'gui_unavailable' }
    $ds4Sessions = @()
    $blocked = $false
    for ($i = 0; $i -lt $application.Children.Count; $i++) {
        try {
            $connection = $application.Children.Item($i)
            if ($connection.DisabledByServer) { $blocked = $true; continue }
            for ($j = 0; $j -lt $connection.Children.Count; $j++) {
                $session = $connection.Children.Item($j)
                if ($session.Busy) { continue }
                $info = $session.Info
                if ([string]$info.SystemName -ceq 'DS4' -and [string]$info.Client -ceq '110') {
                    # A populated User is only checked in memory to exclude the login screen.
                    if (-not [string]::IsNullOrWhiteSpace([string]$info.User)) { $ds4Sessions += $session }
                }
            }
        } catch { $blocked = $true }
    }
    if ($ds4Sessions.Count -eq 0) {
        if ($blocked) { throw 'gui_unavailable' }
        throw 'gui_session_missing'
    }
    if ($ds4Sessions.Count -ne 1) { throw 'gui_session_ambiguous' }
    $selected = $ds4Sessions[0]
    if ($selected.Busy) { throw 'gui_busy' }
    $info = $selected.Info
    # Fixed DS4 endpoint. Load-balanced/unknown endpoints are not silently accepted.
    if ([string]$info.SystemNumber -cne '00') { throw 'gui_endpoint_unverified' }
    $server = [string]$info.ApplicationServer
    if ([string]::IsNullOrWhiteSpace($server)) { throw 'gui_endpoint_unverified' }
    $addresses = @()
    try { $addresses = @([Net.Dns]::GetHostAddresses($server) | ForEach-Object { $_.IPAddressToString }) }
    catch { throw 'gui_endpoint_unverified' }
    if ($server -ne '10.2.22.34' -and $addresses -notcontains '10.2.22.34') { throw 'gui_endpoint_unverified' }
    $snapshot = [ordered]@{
        program = [string]$info.Program
        screen = [string]$info.ScreenNumber
        source = 'SAP GUI session metadata'
        transaction = [string]$info.Transaction
    }
    foreach ($key in @('program', 'screen', 'transaction')) {
        if ($snapshot[$key].Length -gt 128 -or $snapshot[$key] -match '[\x00-\x1F]') { throw 'invalid_evidence' }
    }
    # Reject a session changed by the user while the snapshot was collected.
    $after = $selected.Info
    if ($selected.Busy -or [string]$after.SystemName -cne 'DS4' -or [string]$after.Client -cne '110' -or
        [string]$after.Program -cne $snapshot.program -or [string]$after.ScreenNumber -cne $snapshot.screen -or
        [string]$after.Transaction -cne $snapshot.transaction) { throw 'gui_busy' }
    $content = $snapshot | ConvertTo-Json -Compress
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($content)) | ForEach-Object { $_.ToString('x2') }) }
    finally { $sha.Dispose() }
    $snapshot['_evidence'] = [ordered]@{
        trust = 'UNTRUSTED_EVIDENCE'; instructionAuthority = $false
        system = 'DS4'; client = '110'; command = 'gui_session'; object = 'CURRENT'; contentSha256 = $hash
    }
    return $snapshot
}

try {
    @{ result = (Read-Ds4GuiSnapshot) } | ConvertTo-Json -Depth 8 -Compress
} catch {
    $known = @('gui_unavailable','gui_session_missing','gui_session_ambiguous','gui_busy','gui_endpoint_unverified','invalid_evidence')
    $code = [string]$_.Exception.Message
    if ($known -notcontains $code) { $code = 'gui_unavailable' }
    @{ error_code = $code } | ConvertTo-Json -Compress
    exit 1
}
