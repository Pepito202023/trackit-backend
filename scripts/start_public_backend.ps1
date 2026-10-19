# TrackIt — backend + public HTTPS tunnel (phone on ANY Wi-Fi or mobile data)
#
# Double-click START_TRACKIT.bat in the project folder.

$ErrorActionPreference = "Stop"
$BackendRoot = Split-Path $PSScriptRoot -Parent
$TunnelLog = Join-Path $env:TEMP "trackit_cloudflared.log"

Write-Host ""
Write-Host "=== TrackIt — Any network (mobile data OK) ===" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/3] Starting backend on port 5000..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$BackendRoot'; Write-Host 'TrackIt API — keep open' -ForegroundColor Green; python app.py"
)
Start-Sleep -Seconds 5

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    Write-Host "[!] cloudflared not installed." -ForegroundColor Red
    Write-Host "    winget install Cloudflare.cloudflared" -ForegroundColor Gray
    Write-Host "    Then run START_TRACKIT.bat again." -ForegroundColor Gray
    Read-Host "Press Enter"
    exit 1
}

Write-Host "[2/3] Starting public HTTPS tunnel..." -ForegroundColor Yellow
Remove-Item $TunnelLog -ErrorAction SilentlyContinue

$cf = Start-Process -FilePath "cloudflared" `
    -ArgumentList @("tunnel", "--url", "http://127.0.0.1:5000") `
    -RedirectStandardError $TunnelLog `
    -PassThru -WindowStyle Hidden

$publicUrl = $null
1..40 | ForEach-Object {
    Start-Sleep -Seconds 2
    if (Test-Path $TunnelLog) {
        $log = Get-Content $TunnelLog -Raw -ErrorAction SilentlyContinue
        if ($log -match '(https://[a-zA-Z0-9-]+\.trycloudflare\.com)') {
            $publicUrl = $Matches[1]
        }
    }
    if ($publicUrl) { return }
}

if ($publicUrl) {
    Write-Host "[3/3] Registering URL for all student phones..." -ForegroundColor Yellow
    python "$PSScriptRoot\register_public_url.py" $publicUrl
  try { Set-Clipboard $publicUrl } catch {}
    Write-Host ""
    Write-Host "PUBLIC URL (any network):" -ForegroundColor Green
    Write-Host "  $publicUrl" -ForegroundColor White
    Write-Host ""
    Write-Host "Students: open the app — it should connect automatically." -ForegroundColor Cyan
    Write-Host "If not: Login -> Server connection -> paste URL above -> Save" -ForegroundColor Cyan
    Write-Host ""
} else {
    Write-Host "[3/3] Could not read tunnel URL from log yet." -ForegroundColor Yellow
    Write-Host "      Check $TunnelLog for https://....trycloudflare.com" -ForegroundColor Gray
    Write-Host "      Then run: python backend\scripts\register_public_url.py YOUR_URL" -ForegroundColor Gray
}

Write-Host "Tunnel running (Ctrl+C to stop). Keep backend window open too." -ForegroundColor DarkGray
Write-Host ""
Wait-Process -Id $cf.Id
