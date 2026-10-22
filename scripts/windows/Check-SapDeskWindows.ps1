# SAP Desk Windows prerequisite check. Windows PowerShell 5.1 or later.
# Reads basic OS information and tests only the public SAP Desk health endpoint.
# Does not log in to SAP, read SAP data/credentials, install software, or change
# firewall, certificate trust, execution policy, sleep, or service settings.

function Test-SapDeskWindowsPrerequisites {
    $ErrorActionPreference = 'Stop'
    if ($env:OS -ne 'Windows_NT') {
        throw 'Run this check on the Windows computer that can reach SAP.'
    }

    Write-Host '[1/3] Reading Windows and runtime information...'
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -Property Caption, Version, OSArchitecture
    $machine = Get-CimInstance -ClassName Win32_ComputerSystem -Property SystemType
    $nodeFound = [bool](Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue)
    $sdkConfigured = -not [string]::IsNullOrWhiteSpace($env:SAPNWRFC_HOME)
    $sdkDllPresent = $false
    if ($sdkConfigured) {
        $sdkDllPresent = Test-Path -LiteralPath (Join-Path $env:SAPNWRFC_HOME 'lib\sapnwrfc.dll') -PathType Leaf
    }

    Write-Host '[2/3] Testing direct TCP access to 43.130.36.105:443...'
    $tcpOk = $false
    $tcpError = ''
    $sourceAddress = ''
    try {
        $tcp = Test-NetConnection -ComputerName '43.130.36.105' -Port 443 -InformationLevel Detailed -WarningAction SilentlyContinue
        $tcpOk = [bool]$tcp.TcpTestSucceeded
        if ($null -ne $tcp.SourceAddress) {
            if ($null -ne $tcp.SourceAddress.PSObject.Properties['IPAddress']) {
                $sourceAddress = [string]$tcp.SourceAddress.IPAddress
            } else {
                $sourceAddress = [string]$tcp.SourceAddress
            }
        }
    } catch {
        $tcpError = $_.Exception.GetType().Name
    }

    Write-Host '[3/3] Testing HTTPS health with normal certificate validation...'
    $httpsHealth = 'unavailable'
    $httpsError = ''
    $savedTls = [Net.ServicePointManager]::SecurityProtocol
    try {
        # TLS 1.2 is set for this request process only; the prior setting is restored.
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $health = Invoke-RestMethod -Uri 'https://43.130.36.105/api/health' -Method Get -TimeoutSec 12 -MaximumRedirection 0
        if ($health.status -eq 'ok') {
            $httpsHealth = 'ok'
        } else {
            $httpsHealth = 'unexpected_response'
        }
    } catch {
        # Do not include proxy URLs, credentials, headers, or raw response bodies.
        $httpsError = $_.Exception.GetType().Name
        if ($_.Exception -is [Net.WebException]) {
            $httpsError = [string]$_.Exception.Status
        }
    } finally {
        [Net.ServicePointManager]::SecurityProtocol = $savedTls
    }

    $report = [ordered]@{
        CheckVersion = '1'
        CheckedAt = (Get-Date).ToString('o')
        Windows = [string]$os.Caption
        WindowsVersion = [string]$os.Version
        OSArchitecture = [string]$os.OSArchitecture
        SystemType = [string]$machine.SystemType
        PowerShellVersion = [string]$PSVersionTable.PSVersion
        PowerShellProcess64Bit = [Environment]::Is64BitProcess
        NodeCommandFound = $nodeFound
        SapSdkPathConfigured = $sdkConfigured
        SapSdkDllFilePresent = $sdkDllPresent
        NetworkSourceAddress = $sourceAddress
        CloudDirectTcp443 = $tcpOk
        CloudTcpError = $tcpError
        CloudHttpsHealth = $httpsHealth
        CloudHttpsError = $httpsError
        SapInterfaceConnectivity = 'not_tested'
        SapDataQueries = 0
        CredentialsRead = $false
        ConnectorInstalled = $false
    }
    Write-Host '--- SAP DESK CHECK RESULT ---'
    $report | ConvertTo-Json -Depth 3
    Write-Host '--- END OF RESULT ---'
}

Test-SapDeskWindowsPrerequisites
