#!/usr/bin/env bash
# Read-only token/usage report. Tool: ccusage 20.0.26 (MIT, https://github.com/ccusage/ccusage), pinned.
# Reads ONLY local agent logs (~/.claude/projects/**.jsonl for Claude Code; ~/.codex/sessions for Codex
# via the codex subcommand where supported). --offline: no pricing fetch; no network beyond the one-time
# npm download of the pinned package. Changes no agent setting or behaviour. Disable: stop calling this.
# Examples: fc_usage.sh daily | fc_usage.sh session --json | fc_usage.sh codex daily
set -euo pipefail
exec npx -y ccusage@20.0.26 "$@" --offline
