#!/usr/bin/env bash
#
# fc-context-keeper collector.
#
# Prints live worktree state and RE-VERIFIES the liveness of every PID recorded
# in the previous checkpoint. Read-only against git: no fetch, no index write,
# no ref write, no other worktree. It reads nothing back out of the previous
# checkpoint except the PIDs it is about to re-check.

set -u

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "not a git worktree"; exit 0; }
cd "$ROOT" || exit 0

branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || echo DETACHED)"
slug="$(printf '%s' "$branch" | tr '/ ' '--')"
ctx="$ROOT/.claude/context/$slug.md"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
boot="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)"

echo "# collector output — $now"
echo
echo "VOLATILE worktree : $ROOT"
echo "VOLATILE branch   : $branch"
echo "VOLATILE HEAD     : $(git rev-parse HEAD 2>/dev/null)"
echo "VOLATILE dirty    : $(git status --porcelain 2>/dev/null | grep -c .) entries"
echo "VOLATILE boot_id  : $boot"
echo "VOLATILE uptime   : $(cut -d. -f1 /proc/uptime 2>/dev/null)s"

echo
echo "## durability — what actually survives container loss"
up="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo '(no upstream)')"
echo "  upstream        : $up"
if [ "$up" != "(no upstream)" ]; then
  ahead="$(git rev-list --count "$up"..HEAD 2>/dev/null || echo '?')"
  echo "  unpushed commits: $ahead   <- these exist ONLY in this container"
fi
echo "  uncommitted     : $(git status --porcelain 2>/dev/null | grep -c .) entries <- these exist ONLY in this container"
asref="refs/heads/fc-autosave/$branch"
if git rev-parse --verify --quiet "$asref" >/dev/null 2>&1; then
  echo "  autosave local  : $(git rev-parse --short "$asref")"
  echo "  autosave remote : $(git ls-remote origin "$asref" 2>/dev/null | cut -c1-12 || echo '(unreachable)')"
else
  echo "  autosave        : no snapshot ref for this branch yet"
fi

if [ "$branch" = "DETACHED" ]; then
  echo
  echo "!! DETACHED HEAD — this worktree may be a pinned run. Do not commit or move HEAD."
fi
if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
  echo
  echo "!! ON $branch — do not mutate unless specifically authorized."
fi

echo
echo "## other worktrees (do not mutate)"
git worktree list 2>/dev/null | sed 's/^/  /'

echo
echo "## PID re-verification (the previous checkpoint's claims are DISCARDED, not trusted)"
if [ -f "$ctx" ]; then
  pids="$(grep -oE 'pid[[:space:]]+[0-9]+' "$ctx" 2>/dev/null | grep -oE '[0-9]+' | sort -u)"
  prev_boot="$(grep -oE 'boot_id[^0-9a-f]*[0-9a-f-]{36}' "$ctx" 2>/dev/null | grep -oE '[0-9a-f-]{36}' | head -1)"
  if [ -n "$prev_boot" ] && [ "$prev_boot" != "$boot" ]; then
    echo "  !! boot_id CHANGED since the last checkpoint ($prev_boot -> $boot)."
    echo "     The container restarted. EVERY process from that checkpoint is dead and"
    echo "     every local-only file from it is gone. Treat all of it as lost until proven otherwise."
  fi
  if [ -z "$pids" ]; then
    echo "  previous checkpoint recorded no PIDs"
  else
    for p in $pids; do
      # If the container restarted, a PID recorded under the OLD boot_id cannot
      # refer to the same process, no matter what /proc says now -- the kernel
      # recycles PID numbers from 1 on every boot. Fail closed and report DEAD.
      if [ -n "$prev_boot" ] && [ "$prev_boot" != "$boot" ]; then
        echo "  pid $p DEAD — recorded under boot_id $prev_boot, container has since restarted."
        echo "      Any /proc entry for $p now is a DIFFERENT, recycled process. Do not trust it."
        continue
      fi
      if [ -r "/proc/$p/stat" ]; then
        start="$(awk '{print $22}' "/proc/$p/stat" 2>/dev/null)"
        cmd="$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | cut -c1-100)"
        # Same-boot PID recycling: the kernel reuses numbers, so a live /proc
        # entry does NOT prove it is the same process. starttime disambiguates.
        # If the checkpoint recorded one for this pid, it must match.
        want_start="$(grep -oE "pid[[:space:]]+$p\b[^\n]*starttime[^0-9]*[0-9]+" "$ctx" 2>/dev/null \
                      | grep -oE 'starttime[^0-9]*[0-9]+' | grep -oE '[0-9]+$' | head -1)"
        if [ -n "$want_start" ] && [ "$want_start" != "$start" ]; then
          echo "  pid $p RECYCLED — a live process exists but starttime $start != recorded $want_start."
          echo "      The process from the checkpoint is DEAD; this is an unrelated process reusing the number."
          continue
        fi
        if [ -z "$cmd" ]; then
          echo "  pid $p UNVERIFIABLE — live but its cmdline is empty (kernel thread, or a process"
          echo "      this session cannot inspect). Do NOT treat this as the checkpoint's process."
          echo "      starttime=$start boot_id=$boot"
          continue
        fi
        echo "  pid $p ALIVE as of $now (starttime=$start boot_id=$boot)"
        echo "      cmd: $cmd"
        if [ -z "$want_start" ]; then
          echo "      WARNING: the checkpoint recorded no starttime for this pid, so this is a"
          echo "      NAME match, not an identity match. Record 'pid $p starttime $start' next time."
        fi
      else
        echo "  pid $p DEAD as of $now — delete any line in the checkpoint that says otherwise"
      fi
    done
  fi
else
  echo "  no previous checkpoint at $ctx (first checkpoint for this branch)"
fi

echo
echo "## target"
echo "  write/replace: $ctx"
echo "  gitignored on purpose — it describes one worktree at one moment"
