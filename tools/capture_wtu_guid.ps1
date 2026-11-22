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

Write-Host "     probing ${camIp}:${camPort} ..."
$probe = Test-NetConnection -ComputerName $camIp -Port $camPort -InformationLevel Quiet -WarningAction SilentlyContinue
if ($probe) {
    Write-Host "     camera port is OPEN (good)" -ForegroundColor Green
} else {
    Write-Host "     camera port did not answer. Capture will still run." -ForegroundColor Yellow
}

Write-Host "1/5  Closing any running WTU (the next start must make a NEW connection) ..."
Get-Process -Name "WirelessTransmitterUtility" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

Write-Host "2/5  Set packet filter (TCP 15740) ..."
pktmon filter remove | Out-Null
pktmon filter add PTPIP -p 15740 -t TCP

Write-Host "3/5  Start capture ..."
# Delete both the etl and any stale pcap, otherwise a failed run would leave the
# old pcap behind and we could mistake it for a fresh capture.
if (Test-Path "$out\wtu.etl") { Remove-Item "$out\wtu.etl" -Force }
if (Test-Path "$out\wtu.pcap") { Remove-Item "$out\wtu.pcap" -Force }
# NOTE: the flag is --file-name (not --file).
pktmon start --capture --pkt-size 0 --file-name "$out\wtu.etl"
Start-Sleep -Seconds 2
if (-not (Test-Path "$out\wtu.etl")) {
    Write-Host "ERROR: capture did not start (no etl file)." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 2
}
Write-Host "     capture is running, target file $out\wtu.etl"

# Self-test: make one bare TCP connection to the camera port while capturing.
# If this shows up, we know the capture pipeline works, so an empty result later
# means "WTU never connected" rather than "pktmon is broken".
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect($camIp, $camPort, $null, $null)
    if ($iar.AsyncWaitHandle.WaitOne(3000)) {
        $tcp.EndConnect($iar)
        Write-Host "     self-test connect: OK (SYN should be in the capture)" -ForegroundColor Green
    } else {
        Write-Host "     self-test connect: timeout (camera not reachable)" -ForegroundColor Yellow
    }
    $tcp.Close()
} catch {
    Write-Host "     self-test connect failed: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "4/5  Launching WTU ..."
if (Test-Path $wtu) {
    Start-Process $wtu
} else {
    Write-Host "WARNING: WTU not found at the expected path." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== NOW DO THIS IN THE WTU WINDOW ===" -ForegroundColor Cyan
Write-Host "  a) Hardware type: choose [Camera built-in Wi-Fi]" -ForegroundColor Cyan
Write-Host "     (NOT wired LAN and NOT wireless LAN - those never reach the camera)" -ForegroundColor Cyan
Write-Host "  b) Click Next until your camera appears, then connect" -ForegroundColor Cyan
Write-Host "  c) Destination folder: anything, just get connected" -ForegroundColor Cyan
Write-Host "  d) Once WTU is connected (it may show a file list), come back here" -ForegroundColor Cyan
Write-Host ""
Write-Host "Capture keeps running until you press Enter. No time limit." -ForegroundColor Green
Write-Host ""

Read-Host "Press Enter ONLY AFTER the WTU wizard has finished connecting"

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
