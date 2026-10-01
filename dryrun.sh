#!/usr/bin/env bash
# Local dry run: loads .env, never posts anything. Usage: ./dryrun.sh [live|picks] [tour]
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "No .env — copy .env.example to .env and add DATAGOLF_API_KEY + ANTHROPIC_API_KEY"; exit 1; }
set -a; . ./.env; set +a
for k in DATAGOLF_API_KEY ANTHROPIC_API_KEY; do [ -n "${!k:-}" ] || { echo "Missing $k in .env"; exit 1; }; done
PY="${PY:-$HOME/Scratch Sheet Golf/scratch-sheet-automation/scratch-sheet/venv/bin/python}"
"$PY" pipeline.py "${1:-picks}" --tour "${2:-pga}" --dry-run
