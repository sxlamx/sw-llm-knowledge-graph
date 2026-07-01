#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$DIR/env/.env"
ENV_ARG=(); [[ -f "$ENV_FILE" ]] && ENV_ARG=(--env-file "$ENV_FILE")
docker compose -f "$DIR/docker-compose.yml" "${ENV_ARG[@]}" build "$@"
