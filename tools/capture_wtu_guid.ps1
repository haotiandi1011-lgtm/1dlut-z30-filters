# Capture one PTP/IP handshake between WTU and the camera, to extract the paired GUID.
# Must run as Administrator; the PC should already be connected to the camera Wi-Fi.
#
# Usage (Administrator PowerShell):
#   powershell -ExecutionPolicy Bypass -File "D:\Z30Tools\capture_wtu.ps1"
#
# NOTE: this file is intentionally 100% ASCII. Windows PowerShell 5.1 reads a
# BOM-less .ps1 as ANSI/GBK, so any Chinese text inside would be mis-decoded and
# break the parser. All user-facing text is English on purpose.

$ErrorActionPreference = "Continue"
$out = "D:\Z30Tools\capture"
$wtu = "C:\Program Files\Nikon\Wireless Transmitter Utility\WirelessTransmitterUtility.exe"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "ERROR: this script must run as Administrator." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }
}

Assert-Admin
New-Item -ItemType Directory -Force -Path $out | Out-Null

Write-Host "0/5  Closing any running WTU (the next start must make a NEW connection) ..."
Get-Process -Name "WirelessTransmitterUtility" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

Write-Host "1/5  Set packet filter (TCP 15740) ..."
pktmon filter remove | Out-Null
pktmon filter add PTPIP -p 15740 -t TCP

Write-Host "2/5  Start capture ..."
# Delete both the etl and any stale pcap, otherwise a failed run would leave the
# old pcap behind and we could mistake it for a fresh capture.
if (Test-Path "$out\wtu.etl") { Remove-Item "$out\wtu.etl" -Force }
if (Test-Path "$out\wtu.pcap") { Remove-Item "$out\wtu.pcap" -Force }
# NOTE: the flag is --file-name (not --file).
pktmon start --capture --pkt-size 0 --file-name "$out\wtu.etl"
Start-Sleep -Seconds 2
if (-not (Test-Path "$out\wtu.etl")) {
    Write-Host "ERROR: capture did not start (no etl file). Are you Administrator?" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 2
}

Write-Host "3/5  Launching WTU ..."
if (Test-Path $wtu) {
    Start-Process $wtu
} else {
    Write-Host "WARNING: WTU not found at the expected path." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== NOW DO THIS IN THE WTU WINDOW ===" -ForegroundColor Cyan
Write-Host "  a) Hardware type: choose [Camera built-in Wi-Fi]" -ForegroundColor Cyan
Write-Host "     (NOT wired LAN / NOT wireless LAN - those never reach the camera)" -ForegroundColor Cyan
Write-Host "  b) Click Next until your camera is listed, then connect" -ForegroundColor Cyan
Write-Host "  c) Destination folder: anything, just get connected" -ForegroundColor Cyan
Write-Host ""
Write-Host "Capture runs for 120 seconds, starting now." -ForegroundColor Cyan
Write-Host ""

$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $left = [int]($deadline - (Get-Date)).TotalSeconds
    Write-Host ("  {0}s left ..." -f $left)
}

Write-Host "4/5  Stop capture and convert ..."
pktmon stop | Out-Null
Start-Sleep -Seconds 1

if (Test-Path "$out\wtu.etl") {
    pktmon etl2pcap "$out\wtu.etl" -o "$out\wtu.pcap" | Out-Null
    if (Test-Path "$out\wtu.pcap") {
        $size = (Get-Item "$out\wtu.pcap").Length
        Write-Host ""
        Write-Host "DONE: $out\wtu.pcap  ($size bytes)" -ForegroundColor Green
        Write-Host "Now switch Wi-Fi back to your normal network and tell me it is done."
    } else {
        Write-Host "ERROR: pcap was not produced." -ForegroundColor Red
    }
} else {
    Write-Host "ERROR: capture file missing." -ForegroundColor Red
}

Write-Host ""
Read-Host "Press Enter to close"
