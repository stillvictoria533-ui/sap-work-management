# Windows PowerShell 5.1, no Node/SDK/ChatGPT installation required.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
. (Join-Path $PSScriptRoot 'Connector.Core.ps1')
$script:Token = ''
$originalTls = [Net.ServicePointManager]::SecurityProtocol
try {
    if ($env:OS -ne 'Windows_NT' -or $PSVersionTable.PSVersion.Major -lt 5) { throw 'unsupported_runtime' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Write-Host 'SAP Desk Windows portable pilot - SAP GUI session metadata only'
    Write-Host 'Log in to DS4/110 in SAP GUI first. Keep one DS4 session open.'
    Write-Host 'This does not enable SAP GUI Scripting or change SAP/security settings.'
    Write-Host 'Server must use HTTPS; loopback HTTP is only for a same-PC preview.'
    $script:Origin = Get-SapDeskOrigin (Read-Host 'SAP Desk server URL')
    $code = (Read-Host 'One-time pairing code from the SAP Desk page').Trim()
    if ($code -cnotmatch '^[A-Za-z0-9_-]{32}$') { throw 'invalid_pair_code' }
    $paired = Invoke-SapDeskRequest $script:Origin 'pair' @{code=$code;name='Windows SAP GUI portable';adapter='sap-gui-windows'}
    $code = ''
    if ($paired.protocol -cne 'sapdesk-ds4-v1' -or $paired.token -cnotmatch '^[A-Za-z0-9_-]{43}$' -or
        $paired.device.adapter -cne 'sap-gui-windows') { throw 'protocol_mismatch' }
    $script:Token = $paired.token
    $paired = $null
    $script:LastPulse = [DateTime]::UtcNow
    Write-Host 'Paired. Submit a current-session query on the page. Ctrl+C stops the connector.'
    while ($true) {
        $response = Invoke-SapDeskRequest $script:Origin 'agent/claim' @{} $script:Token
        $script:LastPulse = [DateTime]::UtcNow
        if (-not $response.job) { Start-Sleep -Seconds 2; continue }
        $job = $response.job
        Assert-SapDeskGuiJob $job
        $body = @{error_code='declined'}
        try {
            if (Read-SapDeskConfirmation $job) {
                Assert-SapDeskGuiJob $job
                $body = Invoke-SapDeskGuiRead
                Assert-SapDeskGuiJob $job
            }
        } catch {
            $code = [string]$_.Exception.Message
            if ($code -in @('authorization_failed','server_error')) { throw }
            if ($code -notin @('timeout','output_limit','invalid_evidence','gui_unavailable')) { $code = 'invalid_evidence' }
            $body = @{error_code=$code}
        }
        $body['lease'] = $job.lease
        $null = Invoke-SapDeskRequest $script:Origin ('agent/queries/' + $job.id + '/complete') $body $script:Token
        Write-Host 'Result/status submitted. Return to SAP Desk to review.'
    }
} catch {
    $known = @('invalid_origin','invalid_pair_code','protocol_mismatch','unsupported_runtime','authorization_failed','server_error','invalid_job')
    $code = [string]$_.Exception.Message
    if ($known -notcontains $code) { $code = 'connector_stopped' }
    Write-Host ('Connector stopped: ' + $code)
    Write-Host 'Check the README. No raw SAP errors or credentials are printed.'
    exit 1
} finally {
    $script:Token = ''
    [Net.ServicePointManager]::SecurityProtocol = $originalTls
}
