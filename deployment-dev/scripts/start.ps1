$dir = Split-Path $PSScriptRoot -Parent
$envFile = "$dir\env\.env"
$envArg = if (Test-Path $envFile) { @("--env-file", $envFile) } else { @() }
docker compose -f "$dir\docker-compose.yml" @envArg up -d @args
Write-Host ""
Write-Host "  API      ->  http://localhost:8009"
Write-Host "  API docs ->  http://localhost:8009/docs"
Write-Host "  Frontend ->  http://localhost:5342"
Write-Host ""
