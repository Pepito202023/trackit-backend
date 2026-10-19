# Updates flutter_app/lib/config/api_config.dart with this PC's Wi-Fi IPv4 (192.168.1.x)
$ErrorActionPreference = "Stop"
$configPath = Join-Path $PSScriptRoot "..\..\flutter_app\lib\config\api_config.dart"
$configPath = (Resolve-Path $configPath).Path

$ip = $null
foreach ($line in (ipconfig)) {
    if ($line -match "IPv4.*:\s*(192\.168\.1\.\d+)") {
        $ip = $Matches[1]
        break
    }
}
if (-not $ip) {
    foreach ($line in (ipconfig)) {
        if ($line -match "IPv4.*:\s*(\d+\.\d+\.\d+\.\d+)") {
            $candidate = $Matches[1]
            if ($candidate -notmatch "^127\." -and $candidate -notmatch "^169\.254\.") {
                $ip = $candidate
                break
            }
        }
    }
}

if (-not $ip) {
    Write-Host "Could not detect IPv4. Run ipconfig manually." -ForegroundColor Red
    exit 1
}

$content = Get-Content $configPath -Raw
$content = $content -replace "_localIp = '[^']*'", "_localIp = '$ip'"
Set-Content -Path $configPath -Value $content -NoNewline

Write-Host ""
Write-Host "Updated api_config.dart -> $ip" -ForegroundColor Green
Write-Host "Rebuild the app or use Attendance -> Server -> $ip" -ForegroundColor Yellow
Write-Host ""
