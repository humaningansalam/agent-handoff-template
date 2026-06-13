#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export WORKSPACE_ROOT="${WORKSPACE_ROOT:-$ROOT}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
cd "$ROOT"

if [[ $# -lt 1 ]]; then
  echo "usage: run_python_module.sh module [args...]" >&2
  exit 2
fi

UV_ARGS=(run --directory "$ROOT")
if [[ -s "$ROOT/.python-version" ]]; then
  UV_ARGS+=(--python "$(cat "$ROOT/.python-version")")
fi

MODULE="$1"
shift
exec uv "${UV_ARGS[@]}" python -m "$MODULE" "$@"
