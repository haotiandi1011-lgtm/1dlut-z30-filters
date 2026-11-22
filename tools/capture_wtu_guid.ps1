# Capture the PTP/IP handshake between WTU and the camera, to extract the paired GUID.
# Must run as Administrator; the PC should already be connected to the camera Wi-Fi.
#
# Usage (Administrator PowerShell):
#   powershell -ExecutionPolicy Bypass -File "D:\Z30Tools\capture_wtu.ps1"
#
# NOTE 1: this file is intentionally 100% ASCII. Windows PowerShell 5.1 reads a
# BOM-less .ps1 as ANSI/GBK, so Chinese text inside would be mis-decoded and
# break the parser.
# NOTE 2: the filter captures ALL traffic to/from the camera IP, not just 15740.
# That way, if WTU talks on another port (discovery, transfer channel), we still
# see it instead of staring at an empty capture.

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

Write-Host "0.5/5 Making sure Nikon discovery services are alive ..."
# WTU cannot see the camera without these two. NkPtpEnumWT3 is the camera
# enumerator (NkPtpEnum.exe): it has ErrorControl=1, so once it dies it never
# comes back by itself, and WTU then just says "check the camera connection"
# without ever opening a TCP connection.
# NEVER kill NkPtpEnum: it is a service, not a helper process.
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

Write-Host "1/5  Closing WTU processes (but NOT the enumerator service) ..."
# The GUI is WirelessTransmitterUtility.exe, but the actual PTP/IP session is held
# by NkPtpipStorage.exe (a helper). Killing only the GUI leaves an established TCP
# session behind, and then the capture never contains an Init handshake.
# So: kill whoever owns a connection to port 15740 first, then kill the two GUI
# side processes by name.
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
$names = @("WirelessTransmitterUtility", "NkPtpipStorage")
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

# If the enumerator had held the session, it was killed above. Bring the discovery
# services back before WTU starts, otherwise WTU has nothing to find the camera with.
Write-Host "     re-checking discovery services after the kill ..."
Ensure-NikonServices

Write-Host "     probing ${camIp}:${camPort} ..."
$probe = Test-NetConnection -ComputerName $camIp -Port $camPort -InformationLevel Quiet -WarningAction SilentlyContinue
if ($probe) {
    Write-Host "     camera port is OPEN (good)" -ForegroundColor Green
} else {
    Write-Host "     camera port did not answer. Capture will still run." -ForegroundColor Yellow
}

Write-Host "2/5  Reset packet filter (all traffic to/from $camIp) ..."
# Clear any capture session left behind by an earlier failed run, otherwise the
# new start silently does nothing. Stop twice with a pause: the first stop may
# still be tearing down while the file handle is released.
pktmon stop | Out-Null
Start-Sleep -Seconds 2
pktmon stop | Out-Null
pktmon filter remove | Out-Null
pktmon filter add CAMERA -i $camIp

Write-Host "3/5  Start capture ..."
# Use a unique file name per run. A leftover pktmon session can keep a handle on
# the old file, so deleting a fixed name sometimes silently fails and we end up
# reading yesterday's capture.
$stamp = Get-Date -Format "HHmmss"
$etl = "$out\wtu-$stamp.etl"
$pcap = "$out\wtu-$stamp.pcap"
foreach ($f in (Get-ChildItem $out -Filter "*.etl" -ErrorAction SilentlyContinue)) {
    Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue
}
foreach ($f in (Get-ChildItem $out -Filter "*.pcap" -ErrorAction SilentlyContinue)) {
    Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue
}
# NOTE: the flag is --file-name (not --file).
pktmon start --capture --pkt-size 0 --file-size 256 --file-name $etl
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pktmon could not start (exit code $LASTEXITCODE)." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 2
}
Write-Host "     pktmon started, target file $etl"

# pktmon creates the etl file lazily, on the first captured packet. If nothing is
# flowing, the file simply does not exist yet - so do not treat that as failure.
# This self-test connect produces the first packets and proves the pipeline works.
$selfLocalPort = 0
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect($camIp, $camPort, $null, $null)
    if ($iar.AsyncWaitHandle.WaitOne(3000)) {
        $tcp.EndConnect($iar)
        try { $selfLocalPort = $tcp.Client.LocalEndPoint.Port } catch { }
        Write-Host "     self-test connect: OK (local port $selfLocalPort)" -ForegroundColor Green
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
Write-Host "  page 3/16  Operation: [Select destination folder] is the only one that" -ForegroundColor Cyan
Write-Host "             can be clicked for built-in Wi-Fi. Use it, then Next to the end." -ForegroundColor Cyan
Write-Host "  If WTU shows an error, take a screenshot of that page - it matters." -ForegroundColor Cyan
Write-Host ""
Write-Host "Watching every TCP connection to $camIp for 300s ..." -ForegroundColor Green
Write-Host "Any new connection on ANY port is reported below." -ForegroundColor Green
Write-Host ""

$newConn = $false
$seen = @{}
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$lastPrint = -1
while ($sw.Elapsed.TotalSeconds -lt 300) {
    $items = Get-NetTCPConnection -RemoteAddress $camIp -State Established -ErrorAction SilentlyContinue
    foreach ($x in $items) {
        $k = "$($x.LocalPort)->$($x.RemotePort)"
        if (-not $seen.ContainsKey($k)) {
            $seen[$k] = $true
            if ($x.LocalPort -ne $selfLocalPort) {
                Write-Host "     connection: local $($x.LocalPort) -> camera $($x.RemotePort) at $([int]$sw.Elapsed.TotalSeconds)s" -ForegroundColor Green
            }
        }
        if ($x.RemotePort -eq $camPort -and $x.LocalPort -ne $selfLocalPort) { $newConn = $true }
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
    Write-Host "     NEW session on port $camPort detected after $([int]$sw.Elapsed.TotalSeconds)s" -ForegroundColor Green
    Write-Host "     capturing 25 more seconds so the handshake is safely inside the file ..."
    Start-Sleep -Seconds 25
} else {
    Write-Host ""
    Write-Host "     no new session on port $camPort within 300s." -ForegroundColor Yellow
    if ($seen.Count -gt 0) {
        Write-Host "     but these connections did happen: $($seen.Keys -join ', ')" -ForegroundColor Yellow
    } else {
        Write-Host "     and no TCP connection to the camera happened at all." -ForegroundColor Yellow
    }
}

Write-Host "5/5  Stop capture and convert ..."
pktmon stop | Out-Null
Start-Sleep -Seconds 2
pktmon filter remove | Out-Null

if (Test-Path $etl) {
    $etlSize = (Get-Item $etl).Length
    Write-Host "     etl: $etlSize bytes"
    # Keep the conversion output: it prints the packet count, which tells us at a
    # glance whether anything was actually captured.
    $conv = pktmon etl2pcap $etl -o $pcap 2>&1
    $conv | ForEach-Object { Write-Host "     $_" }
    if (Test-Path $pcap) {
        $size = (Get-Item $pcap).Length
        Write-Host ""
        Write-Host "DONE: $pcap  ($size bytes)" -ForegroundColor Green
        if ($size -lt 400) {
            Write-Host "NOTE: file is almost empty - nothing was captured on this run." -ForegroundColor Yellow
        }
        Write-Host "Now switch Wi-Fi back to your normal network and tell me it is done."
    } else {
        Write-Host "ERROR: pcap was not produced." -ForegroundColor Red
    }
} else {
    Write-Host "ERROR: capture file missing ($etl)." -ForegroundColor Red
}

Write-Host ""
Read-Host "Press Enter to close"
