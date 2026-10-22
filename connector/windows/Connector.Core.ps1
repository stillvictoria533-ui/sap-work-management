# Portable protocol functions. No SAP reads or requests at import time.
function Get-SapDeskOrigin([string]$Value) {
    $uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri)) { throw 'invalid_origin' }
    $loopback = @('127.0.0.1','localhost','[::1]') -contains $uri.Host
    if ($uri.UserInfo -or $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment -or
        ($uri.Scheme -ne 'https' -and -not ($uri.Scheme -eq 'http' -and $loopback))) { throw 'invalid_origin' }
    return $uri.GetLeftPart([UriPartial]::Authority)
}

function Assert-SapDeskGuiJob($Job) {
    $expected = @('id','system','client','operation','object_name','expires_at','lease') | Sort-Object
    $actual = @($Job.PSObject.Properties.Name) | Sort-Object
    if (($expected -join ',') -cne ($actual -join ',')) { throw 'invalid_job' }
    foreach ($key in $expected) { if ($Job.$key -isnot [string]) { throw 'invalid_job' } }
    if ($Job.id -cnotmatch '^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$' -or
        $Job.system -cne 'DS4' -or $Job.client -cne '110' -or $Job.operation -cne 'gui_session' -or
        $Job.object_name -cne 'CURRENT' -or $Job.lease -cnotmatch '^[A-Za-z0-9_-]{43}$') { throw 'invalid_job' }
    $deadline = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse($Job.expires_at, [ref]$deadline) -or $deadline -le [DateTimeOffset]::UtcNow -or
        $deadline -gt [DateTimeOffset]::UtcNow.AddMinutes(5)) { throw 'invalid_job' }
}

function Invoke-SapDeskRequest([string]$Origin, [string]$Route, $Body, [string]$Token = '') {
    $request = [Net.HttpWebRequest]::Create($Origin + '/api/assistant/connector/' + $Route)
    $request.Method = 'POST'
    $request.ContentType = 'application/json; charset=utf-8'
    $request.AllowAutoRedirect = $false
    $request.Timeout = 10000
    $request.ReadWriteTimeout = 10000
    if ($Token) { $request.Headers['Authorization'] = 'Bearer ' + $Token }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 10 -Compress))
    $request.ContentLength = $bytes.Length
    $response = $null
    try {
        $stream = $request.GetRequestStream()
        try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
        $response = $request.GetResponse()
        if ([int]$response.StatusCode -lt 200 -or [int]$response.StatusCode -ge 300) { throw 'server_error' }
        $reader = New-Object IO.StreamReader($response.GetResponseStream(), [Text.Encoding]::UTF8)
        try {
            $builder = New-Object Text.StringBuilder
            $buffer = New-Object char[] 4096
            while (($count = $reader.Read($buffer, 0, $buffer.Length)) -gt 0) {
                if ($builder.Length + $count -gt 32768) { throw 'server_error' }
                [void]$builder.Append($buffer, 0, $count)
            }
            return ($builder.ToString() | ConvertFrom-Json)
        } finally { $reader.Dispose() }
    } catch [Net.WebException] {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -in @(401,403)) { throw 'authorization_failed' }
        throw 'server_error'
    } finally { if ($response) { $response.Dispose() } }
}

function Invoke-SapDeskPulse {
    if (([DateTime]::UtcNow - $script:LastPulse).TotalSeconds -ge 10) {
        $null = Invoke-SapDeskRequest $script:Origin 'agent/heartbeat' @{} $script:Token
        $script:LastPulse = [DateTime]::UtcNow
    }
}

function Read-SapDeskConfirmation($Job) {
    Write-Host ''
    Write-Host 'Read DS4/110 current GUI session metadata only (transaction/program/screen).'
    Write-Host ('Destination: ' + $script:Origin)
    Write-Host 'No screen content, username or password will be sent. Press Y to agree, N to decline.'
    $until = [DateTime]::UtcNow.AddSeconds(90)
    while ([DateTime]::UtcNow -lt $until) {
        Assert-SapDeskGuiJob $Job
        Invoke-SapDeskPulse
        if ([Console]::KeyAvailable) {
            $key = [Console]::ReadKey($true).Key
            if ($key -eq [ConsoleKey]::Y) { return $true }
            if ($key -eq [ConsoleKey]::N) { return $false }
        }
        Start-Sleep -Milliseconds 200
    }
    return $false
}

function Invoke-SapDeskGuiRead {
    param([switch]$Offline)
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = Join-Path $PSHOME 'powershell.exe'
    $reader = Join-Path $PSScriptRoot 'Read-Ds4Gui.ps1'
    $start.Arguments = '-NoLogo -NoProfile -NonInteractive -STA -File "' + $reader + '"'
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    $started = $false
    try {
        [void]$process.Start()
        $started = $true
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        $until = [DateTime]::UtcNow.AddSeconds(60)
        while (-not $process.HasExited) {
            if (-not $Offline) { Invoke-SapDeskPulse }
            if ([DateTime]::UtcNow -ge $until) { throw 'timeout' }
            Start-Sleep -Milliseconds 200
        }
        $raw = $output.GetAwaiter().GetResult()
        if ([Text.Encoding]::UTF8.GetByteCount($raw) -gt 16384) { throw 'output_limit' }
        $value = $raw | ConvertFrom-Json
        if ($value.error_code) {
            if (@('gui_unavailable','gui_session_missing','gui_session_ambiguous','gui_busy','gui_endpoint_unverified','invalid_evidence') -notcontains $value.error_code) { throw 'invalid_evidence' }
            return @{ error_code = $value.error_code }
        }
        if ($process.ExitCode -ne 0 -or -not $value.result) { throw 'gui_unavailable' }
        $result = $value.result
        $keys = @($result.PSObject.Properties.Name) | Sort-Object
        if (($keys -join ',') -cne '_evidence,program,screen,source,transaction') { throw 'invalid_evidence' }
        if ($result._evidence.system -cne 'DS4' -or $result._evidence.client -cne '110' -or
            $result._evidence.command -cne 'gui_session' -or $result._evidence.object -cne 'CURRENT' -or
            $result._evidence.instructionAuthority -ne $false -or $result._evidence.trust -cne 'UNTRUSTED_EVIDENCE' -or
            $result.source -cne 'SAP GUI session metadata') { throw 'invalid_evidence' }
        return @{ result = $result }
    } finally {
        if ($started -and -not $process.HasExited) { $process.Kill(); [void]$process.WaitForExit(3000) }
        $process.Dispose()
    }
}
