#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec bash "$ROOT/.claude/hooks/run_python_module.sh" tools.hooks.enforce_subagent_final_report "$@"
