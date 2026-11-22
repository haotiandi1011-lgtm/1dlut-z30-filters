# Capture one PTP/IP handshake between WTU and the camera, to extract the paired GUID.
# Must run as Administrator; the PC should already be connected to the camera Wi-Fi.
#
# Usage (Administrator PowerShell):
#   powershell -ExecutionPolicy Bypass -File "D:\Z30Tools\capture_wtu.ps1"
#
# NOTE 1: this file is intentionally 100% ASCII. Windows PowerShell 5.1 reads a
# BOM-less .ps1 as ANSI/GBK, so Chinese text inside would be mis-decoded and
# break the parser.
# NOTE 2: there is no countdown timer. You press Enter when the WTU wizard is
# done, so a slow click-through cannot waste the capture window.

$ErrorActionPreference = "Continue"
$out = "D:\Z30Tools\capture"
$wtu = "C:\Program Files\Nikon\Wireless Transmitter Utility\WirelessTransmitterUtility.exe"
$camIp = "192.168.1.1"
$camPort = 15740

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "ERROR: this script must run as Administrator." -ForegroundColor Red
        Write-Host "Right-click Windows PowerShell -> Run as administrator, then run it again."
        Read-Host "Press Enter to close"
        exit 1
    }
}

Assert-Admin
New-Item -ItemType Directory -Force -Path $out | Out-Null

Write-Host "0/5  Checking Wi-Fi ..."
$wlan = (netsh wlan show interfaces) -join "`n"
$ssidLine = ($wlan -split "`n" | Where-Object { $_ -match "^\s*SSID\s*:" } | Select-Object -First 1)
if ($ssidLine) { Write-Host "     current Wi-Fi: $($ssidLine.Trim())" }
if (-not ($wlan -match "Z_30|Z 30|NIKON_Z")) {
    Write-Host "     WARNING: this does not look like the camera Wi-Fi." -ForegroundColor Yellow
    Write-Host "     Connect to the camera network first, or continue anyway." -ForegroundColor Yellow
}

Write-Host "1/5  Closing every WTU-related process ..."
# The GUI is WirelessTransmitterUtility.exe, but the actual PTP/IP session is held
# by NkPtpipStorage.exe (a helper). Killing only the GUI leaves an established TCP
# session behind, and then the capture never contains an Init handshake.
# So: kill whoever owns a connection to port 15740 first, then kill by name.
$killed = @()
# Only ESTABLISHED sessions. TIME_WAIT leftovers report Owner PID 0 or 4 (System),
# and killing those would be catastrophic. Never kill ourselves either.
$conns = Get-NetTCPConnection -RemotePort $camPort -State Established -ErrorAction SilentlyContinue
foreach ($c in $conns) {
    if ($c.OwningProcess -gt 4 -and $c.OwningProcess -ne $PID -and ($killed -notcontains $c.OwningProcess)) {
        $killed += $c.OwningProcess
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}
$names = @("WirelessTransmitterUtility", "NkPtpipStorage", "NkPtpEnum", "NkMsgSender", "WTMessage", "WTMessageMain")
foreach ($n in $names) {
    Get-Process -Name $n -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Id -ne $PID) {
            if ($killed -notcontains $_.Id) { $killed += $_.Id }
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
if ($killed.Count -gt 0) { Write-Host "     stopped PID(s): $($killed -join ', ')" } else { Write-Host "     nothing was running" }
Start-Sleep -Seconds 3

$left = Get-NetTCPConnection -RemotePort $camPort -State Established -ErrorAction SilentlyContinue
if ($left) {
    Write-Host "ERROR: a connection to port $camPort is still alive:" -ForegroundColor Red
    $left | Select-Object LocalPort, RemoteAddress, RemotePort, State, OwningProcess | Format-Table -AutoSize
    Write-Host "Close WTU manually, then run this script again." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 3
}
Write-Host "     no session to the camera remains (good)"

Write-Host "     probing ${camIp}:${camPort} ..."
$probe = Test-NetConnection -ComputerName $camIp -Port $camPort -InformationLevel Quiet -WarningAction SilentlyContinue
if ($probe) {
    Write-Host "     camera port is OPEN (good)" -ForegroundColor Green
} else {
    Write-Host "     camera port did not answer. Capture will still run." -ForegroundColor Yellow
}

Write-Host "2/5  Reset packet filter (TCP 15740) ..."
# Clear any capture session left behind by an earlier failed run, otherwise the
# new start silently does nothing.
pktmon stop | Out-Null
pktmon filter remove | Out-Null
pktmon filter add PTPIP -p 15740 -t TCP

Write-Host "3/5  Start capture ..."
# Delete both the etl and any stale pcap, otherwise a failed run would leave the
# old pcap behind and we could mistake it for a fresh capture.
if (Test-Path "$out\wtu.etl") { Remove-Item "$out\wtu.etl" -Force }
if (Test-Path "$out\wtu.pcap") { Remove-Item "$out\wtu.pcap" -Force }
# NOTE: the flag is --file-name (not --file).
pktmon start --capture --pkt-size 0 --file-name "$out\wtu.etl"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pktmon could not start (exit code $LASTEXITCODE)." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 2
}
Write-Host "     pktmon started, target file $out\wtu.etl"

# pktmon creates the etl file lazily, on the first captured packet. If nothing is
# flowing, the file simply does not exist yet - so do not treat that as failure.
# This self-test connect produces the first packets and proves the pipeline works.
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect($camIp, $camPort, $null, $null)
    if ($iar.AsyncWaitHandle.WaitOne(3000)) {
        $tcp.EndConnect($iar)
        Write-Host "     self-test connect: OK" -ForegroundColor Green
    } else {
        Write-Host "     self-test connect: timeout (camera not reachable)" -ForegroundColor Yellow
    }
    $tcp.Close()
} catch {
    Write-Host "     self-test connect failed: $($_.Exception.Message)" -ForegroundColor Yellow
}

$waited = 0
while (-not (Test-Path "$out\wtu.etl") -and $waited -lt 10) {
    Start-Sleep -Seconds 1
    $waited++
}
if (Test-Path "$out\wtu.etl") {
    Write-Host "     capture file is being written (after ${waited}s)"
} else {
    Write-Host "     WARNING: no etl file yet; continuing anyway." -ForegroundColor Yellow
}
# Let the self-test socket leave ESTABLISHED, otherwise the detector below could
# mistake our own probe for WTU's session.
Start-Sleep -Seconds 2

Write-Host "4/5  Launching WTU ..."
if (Test-Path $wtu) {
    Start-Process $wtu
} else {
    Write-Host "WARNING: WTU not found at the expected path." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== NOW DO THIS IN THE WTU WINDOW ===" -ForegroundColor Cyan
Write-Host "  page 2/16  Hardware type: [Camera built-in Wi-Fi]" -ForegroundColor Cyan
Write-Host "  page 3/16  Operation: only [Select destination folder] can be clicked." -ForegroundColor Cyan
Write-Host "             That is normal for built-in Wi-Fi; the other two are for" -ForegroundColor Cyan
Write-Host "             WT-4/WT-5 add-on units. Just use the folder one." -ForegroundColor Cyan
Write-Host "  then click Next through to the last page." -ForegroundColor Cyan
Write-Host ""
Write-Host "Waiting for a NEW TCP session to the camera (max 240s) ..." -ForegroundColor Green
Write-Host "You do not need to press anything here; this stops by itself." -ForegroundColor Green
Write-Host ""

$newConn = $false
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$lastPrint = -1
while ($sw.Elapsed.TotalSeconds -lt 240) {
    $c = Get-NetTCPConnection -RemotePort $camPort -State Established -ErrorAction SilentlyContinue
    if ($c) { $newConn = $true; break }
    Start-Sleep -Milliseconds 400
    $sec = [int]$sw.Elapsed.TotalSeconds
    if ($sec -ne $lastPrint -and ($sec % 10) -eq 0) {
        $lastPrint = $sec
        Write-Host "     ${sec}s ..."
    }
}

if ($newConn) {
    Write-Host ""
    Write-Host "     NEW session to the camera detected after $([int]$sw.Elapsed.TotalSeconds)s" -ForegroundColor Green
    Write-Host "     capturing 20 more seconds so the handshake is safely inside the file ..."
    Start-Sleep -Seconds 20
} else {
    Write-Host ""
    Write-Host "     no new session appeared within 240s." -ForegroundColor Yellow
    Write-Host "     The wizard probably never got to the connect step." -ForegroundColor Yellow
}

Write-Host "5/5  Stop capture and convert ..."
pktmon stop | Out-Null
Start-Sleep -Seconds 1
pktmon filter remove | Out-Null

if (Test-Path "$out\wtu.etl") {
    $etlSize = (Get-Item "$out\wtu.etl").Length
    Write-Host "     etl: $etlSize bytes"
    pktmon etl2pcap "$out\wtu.etl" -o "$out\wtu.pcap" | Out-Null
    if (Test-Path "$out\wtu.pcap") {
        $size = (Get-Item "$out\wtu.pcap").Length
        Write-Host ""
        Write-Host "DONE: $out\wtu.pcap  ($size bytes)" -ForegroundColor Green
        if ($size -lt 400) {
            Write-Host "NOTE: the file is almost empty, so no 15740 traffic was captured." -ForegroundColor Yellow
            Write-Host "      Most likely WTU never connected. Try again and finish the wizard." -ForegroundColor Yellow
        }
        Write-Host "Now switch Wi-Fi back to your normal network and tell me it is done."
    } else {
        Write-Host "ERROR: pcap was not produced." -ForegroundColor Red
    }
} else {
    Write-Host "ERROR: capture file missing." -ForegroundColor Red
}

Write-Host ""
Read-Host "Press Enter to close"
