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
  # Skip the primary checkout: main is committed deliberately, not on a timer.
  [ "$wt" = "/home/user/PROJECT-GRIDIRON" ] && continue
  [ -d "$wt" ] || continue

  branch=$(git -C "$wt" branch --show-current 2>/dev/null) || continue
  [ -n "$branch" ] || continue
  git -C "$wt" diff --quiet && git -C "$wt" diff --cached --quiet \
    && [ -z "$(git -C "$wt" ls-files --others --exclude-standard)" ] && continue

  git -C "$wt" add -A >/dev/null 2>&1
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
