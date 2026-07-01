$dir = Split-Path $PSScriptRoot -Parent
$envFile = "$dir\env\.env"
$envArg = if (Test-Path $envFile) { @("--env-file", $envFile) } else { @() }
docker compose -f "$dir\docker-compose.yml" @envArg build @args
