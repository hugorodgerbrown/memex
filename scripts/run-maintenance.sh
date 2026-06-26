#!/usr/bin/env bash
# Memex maintenance run: refresh every scope's index, then run the dream cycle.
#
# Indexing first ensures the dream pass (dedup, broken-link and salience
# analysis) sees current data. Invoked by cron or the launchd agent; safe to run
# by hand. Logs to $MEMEX_LOG (default /tmp/memex-maintenance.log).
set -euo pipefail

MEMEX_HOME="${MEMEX_HOME:-$HOME/.claude/memex}"
MEMEX_LOG="${MEMEX_LOG:-/tmp/memex-maintenance.log}"

{
  echo "=== memex maintenance $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  uv run --project "$MEMEX_HOME" memex maintain
} >>"$MEMEX_LOG" 2>&1
