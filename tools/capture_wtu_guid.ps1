# Capture the PTP/IP handshake between WTU and the camera, to extract the paired GUID.
# Must run as Administrator.
#
# Usage (Administrator PowerShell):
#   powershell -ExecutionPolicy Bypass -File "D:\Z30Tools\capture_wtu.ps1"
#
# DESIGN RULE: this script NEVER kills anything. Earlier versions stopped
# NkPtpipStorage.exe / WirelessTransmitterUtility.exe, which tore down a perfectly
# healthy session and made the camera fall back to "connecting to computer".
# We do not need to kill: to see an Init packet we only need the camera to
# reconnect once, which the camera does by itself when its Wi-Fi is toggled.
#
# NOTE: file is intentionally 100% ASCII (Windows PowerShell 5.1 reads a BOM-less
# .ps1 as ANSI/GBK and would mangle any Chinese text).

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

Write-Host "0/4  Checking Wi-Fi ..."
$wlan = (netsh wlan show interfaces) -join "`n"
$ssidLine = ($wlan -split "`n" | Where-Object { $_ -match "^\s*SSID\s*:" } | Select-Object -First 1)
if ($ssidLine) { Write-Host "     current Wi-Fi: $($ssidLine.Trim())" } else { Write-Host "     WARNING: not connected to any Wi-Fi" -ForegroundColor Yellow }
if (-not ($wlan -match "Z_30|Z 30|NIKON_Z")) {
    Write-Host "     WARNING: this does not look like the camera Wi-Fi." -ForegroundColor Yellow
    Write-Host "     You MUST be joined to the camera network before running this." -ForegroundColor Yellow
}

Write-Host "0.5/4 Ensuring Nikon discovery services are alive (this is what lets WTU see the camera) ..."
function Ensure-NikonServices {
    foreach ($svcName in @("Nkdnssd Service", "NkPtpEnumWT3")) {
        $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
        if (-not $svc) {
            Write-Host "     $svcName : not installed" -ForegroundColor Yellow
            continue
        }
        if ($svc.Status -eq "Running") {
            Write-Host "     $svcName : running" -ForegroundColor Green
        } else {
            try {
                Start-Service -Name $svcName -ErrorAction Stop
                Start-Sleep -Seconds 1
                $svc.Refresh()
                Write-Host "     $svcName : started (now $($svc.Status))" -ForegroundColor Green
            } catch {
                Write-Host "     $svcName : FAILED to start - $($_.Exception.Message)" -ForegroundColor Red
            }
        }
    }
}
Ensure-NikonServices

Write-Host "1/4  Checking WTU (start it if it is not running, never kill it) ..."
$gui = Get-Process -Name "WirelessTransmitterUtility" -ErrorAction SilentlyContinue
if ($gui) {
    Write-Host "     WTU is already running (PID $($gui[0].Id)) - leaving it alone" -ForegroundColor Green
} else {
    if (Test-Path $wtu) {
        Start-Process $wtu
        Write-Host "     WTU was not running, started it"
        Start-Sleep -Seconds 5
    } else {
        Write-Host "     WARNING: WTU not found at $wtu" -ForegroundColor Yellow
    }
}

# Snapshot existing sessions so we can tell a NEW one from the old one.
$baseline = @{}
foreach ($c in (Get-NetTCPConnection -RemotePort $camPort -State Established -ErrorAction SilentlyContinue)) {
    $baseline[$c.LocalPort] = $true
}
if ($baseline.Count -gt 0) {
    Write-Host "     note: a session already exists (local port(s) $($baseline.Keys -join ', '))"
    Write-Host "     it has no handshake in it, so we need one reconnect" -ForegroundColor Yellow
}

Write-Host "2/4  Start capture ..."
pktmon stop | Out-Null
Start-Sleep -Seconds 2
pktmon stop | Out-Null
pktmon filter remove | Out-Null
pktmon filter add CAMERA -i $camIp

# Unique file per run: a leftover session can hold a handle on a fixed name,
# making Remove-Item fail silently and leaving us reading an old capture.
$stamp = Get-Date -Format "HHmmss"
$etl = "$out\wtu-$stamp.etl"
$pcap = "$out\wtu-$stamp.pcap"
foreach ($f in (Get-ChildItem $out -Filter "*.etl" -ErrorAction SilentlyContinue)) { Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue }
foreach ($f in (Get-ChildItem $out -Filter "*.pcap" -ErrorAction SilentlyContinue)) { Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue }

# NOTE: the flag is --file-name (not --file).
pktmon start --capture --pkt-size 0 --file-size 256 --file-name $etl
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pktmon could not start (exit code $LASTEXITCODE)." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 2
}
Write-Host "     capture file: $etl"

# One ICMP echo creates the etl file (pktmon creates it on the first packet) and
# proves the pipeline works. ICMP does not touch port 15740, so it cannot disturb
# the camera session.
Write-Host "     sending 2 pings to $camIp to make the capture file appear ..."
ping -n 2 -w 1000 $camIp | Out-Null
$waited = 0
while (-not (Test-Path $etl) -and $waited -lt 10) {
    Start-Sleep -Seconds 1
    $waited++
}

Write-Host ""
Write-Host "=== ACTION REQUIRED (in this order) ===" -ForegroundColor Cyan
Write-Host "  1) Leave WTU open on the page it shows now. Do not close it." -ForegroundColor Cyan
Write-Host "  2) On the CAMERA: menu -> Connect to PC -> toggle Wi-Fi connection" -ForegroundColor Cyan
Write-Host "     OFF, wait 3 seconds, then ON again." -ForegroundColor Cyan
Write-Host "  3) That forces a brand new TCP session, and its first packet is the" -ForegroundColor Cyan
Write-Host "     Init Command Request we need." -ForegroundColor Cyan
Write-Host "  Do NOT switch the PC back to your normal Wi-Fi until this prints DONE." -ForegroundColor Yellow
Write-Host ""
Write-Host "Waiting for a NEW session on port $camPort (max 300s) ..." -ForegroundColor Green
Write-Host ""

$newConn = $false
$seen = @{}
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$lastPrint = -1
while ($sw.Elapsed.TotalSeconds -lt 300) {
    $items = Get-NetTCPConnection -RemotePort $camPort -State Established -ErrorAction SilentlyContinue
    foreach ($x in $items) {
        if (-not $baseline.ContainsKey($x.LocalPort)) {
            $k = "$($x.LocalPort)"
            if (-not $seen.ContainsKey($k)) {
                $seen[$k] = $true
                Write-Host "     NEW session: local $($x.LocalPort) -> camera $($x.RemotePort) at $([int]$sw.Elapsed.TotalSeconds)s" -ForegroundColor Green
            }
            $newConn = $true
        }
    }
    if ($newConn) { break }
    Start-Sleep -Milliseconds 400
    $sec = [int]$sw.Elapsed.TotalSeconds
    if ($sec -ne $lastPrint -and ($sec % 20) -eq 0) {
        $lastPrint = $sec
        Write-Host "     ${sec}s ..."
    }
}

if ($newConn) {
    Write-Host ""
    Write-Host "     session detected, capturing 20 more seconds for the handshake ..."
    Start-Sleep -Seconds 20
} else {
    Write-Host ""
    Write-Host "     no new session within 300s." -ForegroundColor Yellow
    Write-Host "     The camera was probably not toggled, or WTU did not reconnect." -ForegroundColor Yellow
}

Write-Host "3/4  Stop capture and convert ..."
pktmon stop | Out-Null
Start-Sleep -Seconds 2
pktmon filter remove | Out-Null

if (Test-Path $etl) {
    Write-Host "     etl: $((Get-Item $etl).Length) bytes"
    # etl2pcap prints a packet count; keep it visible, it is the fastest sanity check.
    $conv = pktmon etl2pcap $etl -o $pcap 2>&1
    $conv | ForEach-Object { Write-Host "     $_" }
    if (Test-Path $pcap) {
        Write-Host ""
        Write-Host "DONE: $pcap  ($((Get-Item $pcap).Length) bytes)" -ForegroundColor Green
        Write-Host "Now switch Wi-Fi back to your normal network and tell me it is done."
    } else {
        Write-Host "ERROR: pcap was not produced." -ForegroundColor Red
    }
} else {
    Write-Host "ERROR: capture file missing ($etl)." -ForegroundColor Red
}

Write-Host ""
Read-Host "Press Enter to close"
