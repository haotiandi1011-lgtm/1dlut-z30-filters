# Capture WTU traffic against the camera. Used for two things:
#   1) grabbing the Init handshake (paired host ID)
#   2) grabbing the PAIRING exchange, when the camera has no paired host yet
#
# Must run as Administrator.
#
# Usage (Administrator PowerShell):
#   powershell -ExecutionPolicy Bypass -File "D:\Z30Tools\capture_wtu.ps1" -HoldSeconds 90
#
# The user has explicitly allowed this script to close WTU processes, so it does:
# WTU must open a NEW TCP connection, otherwise the capture only contains the
# idle event heartbeats and no handshake at all.
# NEVER kill NkPtpEnum: that is the camera enumerator SERVICE (NkPtpEnumWT3,
# ErrorControl=1). Killing it leaves WTU unable to find any camera, and it does
# not restart by itself.
#
# NOTE: file is intentionally 100% ASCII (Windows PowerShell 5.1 reads a BOM-less
# .ps1 as ANSI/GBK and would mangle any Chinese text).

param(
    [int]$HoldSeconds = 90
)

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
if ($ssidLine) { Write-Host "     current Wi-Fi: $($ssidLine.Trim())" } else { Write-Host "     WARNING: no Wi-Fi connection" -ForegroundColor Yellow }
if (-not ($wlan -match "Z_30|Z 30|NIKON_Z")) {
    Write-Host "     WARNING: this does not look like the camera Wi-Fi." -ForegroundColor Yellow
}

Write-Host "0.5/5 Ensuring Nikon discovery services are alive ..."
foreach ($svcName in @("Nkdnssd Service", "NkPtpEnumWT3")) {
    $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
    if (-not $svc) { continue }
    if ($svc.Status -ne "Running") {
        try {
            Start-Service -Name $svcName -ErrorAction Stop
            Start-Sleep -Seconds 1
            Write-Host "     $svcName : started" -ForegroundColor Green
        } catch {
            Write-Host "     $svcName : FAILED - $($_.Exception.Message)" -ForegroundColor Red
        }
    } else {
        Write-Host "     $svcName : running"
    }
}

Write-Host "1/5  Closing WTU so it opens a fresh connection ..."
$names = @("WirelessTransmitterUtility", "NkPtpipStorage")
$killed = @()
foreach ($n in $names) {
    Get-Process -Name $n -ErrorAction SilentlyContinue | ForEach-Object {
        $killed += $_.Id
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}
if ($killed.Count -gt 0) { Write-Host "     stopped PID(s): $($killed -join ', ')" } else { Write-Host "     nothing was running" }
Start-Sleep -Seconds 3

Write-Host "2/5  Reset packet filter (all traffic to/from $camIp) ..."
pktmon stop | Out-Null
Start-Sleep -Seconds 2
pktmon stop | Out-Null
pktmon filter remove | Out-Null
pktmon filter add CAMERA -i $camIp

Write-Host "3/5  Start capture ..."
$stamp = Get-Date -Format "HHmmss"
$etl = "$out\wtu-$stamp.etl"
$pcap = "$out\wtu-$stamp.pcap"
# Do NOT delete other captures here. File names are unique, and a second run
# used to wipe the first run's data (that is exactly how one pairing capture was
# lost). Leaving old files costs a few hundred KB and saves the experiment.

pktmon start --capture --pkt-size 0 --file-size 256 --file-name $etl
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pktmon could not start (exit code $LASTEXITCODE)." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 2
}
Write-Host "     capture file: $etl"

# ICMP makes the etl file appear (pktmon creates it on the first packet) and does
# not touch port 15740, so it cannot disturb the camera session.
ping -n 2 -w 1000 $camIp | Out-Null
$waited = 0
while (-not (Test-Path $etl) -and $waited -lt 10) { Start-Sleep -Seconds 1; $waited++ }

Write-Host "4/5  Launching WTU ..."
if (Test-Path $wtu) { Start-Process $wtu } else { Write-Host "WARNING: WTU not found" -ForegroundColor Yellow }

Write-Host ""
Write-Host "=== DO THIS IN THE WTU WINDOW ===" -ForegroundColor Cyan
Write-Host "  2/16 Hardware type : [Camera built-in Wi-Fi]" -ForegroundColor Cyan
Write-Host "  3/16 Operation     : [Select destination folder] (only clickable one)" -ForegroundColor Cyan
Write-Host "  Then Next until it tries to connect." -ForegroundColor Cyan
Write-Host "  If the camera shows a confirmation code, enter it in WTU." -ForegroundColor Cyan
Write-Host ""
Write-Host "Waiting for a NEW session on port $camPort (max 300s) ..." -ForegroundColor Green
Write-Host "Do NOT switch the PC back to the normal Wi-Fi until this prints DONE." -ForegroundColor Yellow
Write-Host ""

$newConn = $false
$seen = @{}
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$lastPrint = -1
while ($sw.Elapsed.TotalSeconds -lt 300) {
    $items = Get-NetTCPConnection -RemotePort $camPort -State Established -ErrorAction SilentlyContinue
    foreach ($x in $items) {
        $k = "$($x.LocalPort)"
        if (-not $seen.ContainsKey($k)) {
            $seen[$k] = $true
            Write-Host "     session: local $($x.LocalPort) at $([int]$sw.Elapsed.TotalSeconds)s" -ForegroundColor Green
        }
        $newConn = $true
    }
    if ($newConn) { break }
    Start-Sleep -Milliseconds 400
    $sec = [int]$sw.Elapsed.TotalSeconds
    if ($sec -ne $lastPrint -and ($sec % 20) -eq 0) { $lastPrint = $sec; Write-Host "     ${sec}s ..." }
}

if ($newConn) {
    Write-Host ""
    Write-Host "     session up, holding capture for $HoldSeconds s so the whole exchange is recorded ..."
    $deadline = (Get-Date).AddSeconds($HoldSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $left = [int]($deadline - (Get-Date)).TotalSeconds
        Write-Host ("     holding {0}s ..." -f $left)
    }
} else {
    Write-Host ""
    Write-Host "     no session within 300s" -ForegroundColor Yellow
}

Write-Host "5/5  Stop capture and convert ..."
pktmon stop | Out-Null
Start-Sleep -Seconds 2
pktmon filter remove | Out-Null

if (Test-Path $etl) {
    Write-Host "     etl: $((Get-Item $etl).Length) bytes"
    $conv = pktmon etl2pcap $etl -o $pcap 2>&1
    $conv | ForEach-Object { Write-Host "     $_" }
    if (Test-Path $pcap) {
        Write-Host ""
        Write-Host "DONE: $pcap  ($((Get-Item $pcap).Length) bytes)" -ForegroundColor Green
        Write-Host "Now switch Wi-Fi back and tell me it is done."
    } else {
        Write-Host "ERROR: pcap was not produced." -ForegroundColor Red
    }
} else {
    Write-Host "ERROR: capture file missing ($etl)." -ForegroundColor Red
}

Write-Host ""
Read-Host "Press Enter to close"
