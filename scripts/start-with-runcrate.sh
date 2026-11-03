#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if ! command -v runcrate >/dev/null 2>&1; then
  printf 'Runcrate CLI was not found. Install it and run `runcrate login` first.\n' >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  printf 'Docker was not found. Install Docker and retry.\n' >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  printf 'Docker is not running or this account cannot access the Docker daemon.\n' >&2
  exit 1
fi

bridge_host="$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)"
if [[ -z "$bridge_host" ]]; then
  printf 'Could not determine the Docker bridge gateway address.\n' >&2
  exit 1
fi

token_file="$PROJECT_ROOT/.runcrate-bridge-token"
if [[ ! -s "$token_file" ]]; then
  if ! command -v openssl >/dev/null 2>&1; then
    printf 'OpenSSL is required to create a private token for the local bridge.\n' >&2
    exit 1
  fi
  (umask 077; openssl rand -hex 32 > "$token_file")
fi
chmod 600 "$token_file"

python_bin="${PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3 || true)"
fi
if [[ -z "$python_bin" ]]; then
  printf 'Python 3 was not found. Create the project virtual environment and retry.\n' >&2
  exit 1
fi

"$python_bin" scripts/runcrate_bridge.py --host "$bridge_host" --token-file "$token_file" &
bridge_pid=$!
cleanup() {
  kill "$bridge_pid" 2>/dev/null || true
  wait "$bridge_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

sleep 0.2
if ! kill -0 "$bridge_pid" 2>/dev/null; then
  printf 'The authenticated Runcrate bridge failed to start.\n' >&2
  exit 1
fi

printf 'Starting the Docker UI. Runcrate CLI commands will use this laptop'\''s `runcrate login` session.\n'
docker compose up --build
