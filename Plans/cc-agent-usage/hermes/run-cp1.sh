#!/usr/bin/env bash
# CC-agent-usage CP1 runner — hermes side. Read-only analysis worktrees + one detached hermes chat.
set -u

RUN="${1:?usage: run.sh <run-id> <prompt-file>}"
PROMPT_FILE="${2:?usage: run.sh <run-id> <prompt-file>}"
BASE="/tmp/$RUN"

mkdir -p "$BASE" || exit 1

# 1. Read-only detached worktrees, one per repo, named after the repo.
if [ ! -d "$BASE/tokenInspector" ]; then
  git -C "$HOME/Projects/tokenInspector" worktree add --detach "$BASE/tokenInspector" 9bdc8b5 \
    > "$BASE/worktree-backend.log" 2>&1 || { echo "WORKTREE-BACKEND-FAIL"; cat "$BASE/worktree-backend.log"; exit 2; }
fi
if [ ! -d "$BASE/token_inspector" ]; then
  git -C "$HOME/Projects/token_inspector" worktree add --detach "$BASE/token_inspector" 7a6d094 \
    > "$BASE/worktree-plugin.log" 2>&1 || { echo "WORKTREE-PLUGIN-FAIL"; cat "$BASE/worktree-plugin.log"; exit 3; }
fi

# 2. Prompt with the run id substituted.
sed "s|<RUN>|$RUN|g" "$PROMPT_FILE" > "$BASE/prompt.txt" || exit 4
if grep -q '<RUN>' "$BASE/prompt.txt"; then echo "PLACEHOLDER-LEFT"; exit 5; fi

# 3. Detached run so ssh can return immediately.
cd "$BASE" || exit 6
setsid nohup "$HOME/.local/bin/hermes" chat --provider openai-codex --model gpt-6-astra -Q --max-turns 80 \
  -q "$(cat "$BASE/prompt.txt")" </dev/null > "$BASE/out.log" 2>&1 &
echo $! > "$BASE/pid"

sleep 3
echo "RUN=$RUN"
echo "PID=$(cat "$BASE/pid")"
echo "WORKTREES=$(git -C "$HOME/Projects/tokenInspector" worktree list | wc -l)"
echo "STARTED"
