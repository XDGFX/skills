#!/bin/sh
# Runs the gate's hook under uv, so it gets the Python its script header asks for rather than
# macOS's own python3. Usage: gate.sh pre|post|bash, with the hook's event on stdin.
mode=$1
event=$(cat)
if [ "$mode" = bash ]; then
    # Every Bash call comes here: only a subagent's gate command needs the gate.
    case "$event" in *fusion_gate.py*) ;; *) exit 0 ;; esac
    case "$event" in *'"agent_id"'*) ;; *) exit 0 ;; esac
fi
script="$(dirname "$0")/../fusion_gate.py"

fail() {
    [ "$mode" = bash ] && exit 0
    echo "fusion-gate: $1, so this Fusion call was NOT run. Tell Cal: don't work around the gate." >&2
    exit 2
}

candidates=${FUSION_GATE_UV_CANDIDATES-"/opt/homebrew/bin/uv $HOME/.local/bin/uv /usr/local/bin/uv"}
for uv in $(command -v uv 2>/dev/null) $candidates; do
    [ -x "$uv" ] || continue
    printf '%s' "$event" | "$uv" run --quiet --script "$script" hook && exit 0
    fail "uv couldn't run the gate (exit $?; try \`uv run --script $script status\`)"
done
fail "uv isn't installed (brew install uv), and the gate needs it to run"
