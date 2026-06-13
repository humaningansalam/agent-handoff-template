#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec bash "$ROOT/.claude/hooks/run_python_module.sh" tools.hooks.capture_subagent_tool_event "$@"
