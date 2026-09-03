#!/usr/bin/env bash
# Periodically commit (and push) every agent worktree that has uncommitted work.
#
# WHY THIS EXISTS. Agents in this project have twice been killed mid-task by a
# session limit. Both times the instruction "commit after each item" was either
# absent or unhonoured, because a process that gets killed cannot run a commit
# on the way out -- an instruction in a prompt is a request, not a guarantee.
# The first incident cost nothing (recovered from the worktree by hand); the
# second left 2143 uncommitted lines that only survived because the worktree
# outlived the agent and someone went looking for it.
#
# This runs OUTSIDE the agents, so it does not care whether they cooperate,
# finish, crash, or get killed. Commits are tagged [autosave] so real agent
# commits stay distinguishable in history, and each worktree commits to its own
# branch so autosaves can never collide with each other or with main.
#
# It also PUSHES. The container this runs in is ephemeral and is reclaimed
# after inactivity, which takes local-only commits with it -- so a commit that
# was never pushed is only half a safeguard.
set -uo pipefail
cd /home/user/PROJECT-GRIDIRON || exit 1

git worktree list --porcelain | awk '/^worktree /{print $2}' | while read -r wt; do
  # The primary checkout gets a NARROWER rule, applied further down: only
  # untracked (brand-new) files are autosaved there, never modifications to
  # tracked files. An agent spawned without worktree isolation writes straight
  # into main, so leaving main entirely unprotected loses that agent's work --
  # but auto-committing edits to existing tracked code on the primary branch
  # would be far worse than the problem it solves.
  primary=0
  [ "$wt" = "/home/user/PROJECT-GRIDIRON" ] && primary=1
  [ -d "$wt" ] || continue

  branch=$(git -C "$wt" branch --show-current 2>/dev/null) || continue
  [ -n "$branch" ] || continue
  git -C "$wt" diff --quiet && git -C "$wt" diff --cached --quiet \
    && [ -z "$(git -C "$wt" ls-files --others --exclude-standard)" ] && continue

  if [ "$primary" -eq 1 ]; then
    # New files only. Anything already tracked is deliberate work in progress
    # and belongs to whoever is editing it.
    untracked=$(git -C "$wt" ls-files --others --exclude-standard)
    [ -n "$untracked" ] || continue
    echo "$untracked" | while read -r f; do
      [ -n "$f" ] || continue
      # SIZE GATE. Source is small; generated data is big. Without this the
      # watchdog tracks regenerable artifacts, and once a file is tracked
      # .gitignore no longer applies to it, so it keeps getting committed on
      # a 3-minute timer forever. This has already happened twice: a 3.4MB
      # Statcast parquet cache and a 6.1MB backtest rows.jsonl, both of which
      # had gitignore rules added only AFTER the watchdog had already picked
      # them up. Losing an unsaved multi-megabyte generated file is cheap --
      # it regenerates. Losing unsaved source is not, and source is never
      # this large.
      sz=$(stat -c%s "$wt/$f" 2>/dev/null || echo 0)
      if [ "$sz" -gt 1048576 ]; then
        echo "$(date -u +%H:%M:%S)   skipped $f ($((sz/1024))KB — regenerable, not source)"
        continue
      fi
      git -C "$wt" add -- "$f" >/dev/null 2>&1
    done
  else
    git -C "$wt" add -A >/dev/null 2>&1
  fi
  if git -C "$wt" diff --cached --quiet; then continue; fi

  n=$(git -C "$wt" diff --cached --numstat | wc -l)
  if git -C "$wt" -c user.name="autosave" -c user.email="actions@github.com" \
       commit -q -m "[autosave] work in progress on $branch ($n files)

Committed automatically by .claude/worktree-autosave.sh, not by the agent.
Exists so a session limit or crash cannot destroy uncommitted work. Safe to
squash or discard once the agent's real commits land."; then
    echo "$(date -u +%H:%M:%S) autosaved $n file(s) on $branch"
    git -C "$wt" push -q -u origin "$branch" >/dev/null 2>&1 \
      && echo "$(date -u +%H:%M:%S)   pushed $branch" \
      || echo "$(date -u +%H:%M:%S)   push failed for $branch (commit is safe locally)"
  fi
done
