#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
TARGET_DIR="$CODEX_HOME/lingling"
HOOKS_FILE="$CODEX_HOME/hooks.json"

PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
  echo "Lingling needs Python 3. Install Python 3, then run this installer again."
  exit 1
fi

mkdir -p "$TARGET_DIR"
cp "$ROOT_DIR/lingling.py" "$TARGET_DIR/lingling.py"
chmod +x "$TARGET_DIR/lingling.py"

if [[ -f "$HOOKS_FILE" ]]; then
  cp "$HOOKS_FILE" "$HOOKS_FILE.lingling-backup.$(date +%Y%m%d-%H%M%S)"
fi

"$PYTHON" - "$HOOKS_FILE" "$PYTHON" "$TARGET_DIR/lingling.py" <<'PY'
import json
import shlex
import sys
from pathlib import Path

hooks_path = Path(sys.argv[1])
python = sys.argv[2]
script = sys.argv[3]

try:
    data = json.loads(hooks_path.read_text(encoding="utf-8")) if hooks_path.exists() else {}
except Exception as exc:
    raise SystemExit(f"Could not parse {hooks_path}: {exc}")

hooks = data.setdefault("hooks", {})
command = f"{shlex.quote(python)} {shlex.quote(script)}"

for event in ("Stop", "PermissionRequest", "SubagentStop"):
    groups = hooks.setdefault(event, [])

    already = False
    for group in groups:
        for handler in group.get("hooks", []):
            if "lingling/lingling.py" in str(handler.get("command", "")):
                already = True
                break
        if already:
            break

    if not already:
        groups.append({
            "hooks": [{
                "type": "command",
                "command": command,
                "async": True,
            }]
        })

hooks_path.parent.mkdir(parents=True, exist_ok=True)
hooks_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Updated {hooks_path}")
PY

# Install a natural online neural voice into Lingling's own venv.
# If this fails, the plugin still works with macOS `say`.
VENV="$TARGET_DIR/venv"
echo "Setting up neural voice (Edge TTS)..."
if "$PYTHON" -m venv "$VENV" >/dev/null 2>&1; then
  if "$VENV/bin/python" -m pip install --upgrade --quiet pip edge-tts >/dev/null 2>&1; then
    echo "Neural voice ready."
  else
    echo "Edge TTS install failed; Lingling will use the macOS say voice instead."
  fi
else
  echo "Could not create a venv; Lingling will use the macOS say voice instead."
fi

# Hooks are enabled by default in current Codex, but this helps older compatible builds.
if command -v codex >/dev/null 2>&1; then
  codex features enable hooks >/dev/null 2>&1 || true
fi

echo
echo "Lingling for Codex installed."
echo "Hooks: $HOOKS_FILE"
echo "App:   $TARGET_DIR/lingling.py"
echo
echo "Test the voices with:"
echo "  $PYTHON $TARGET_DIR/lingling.py --test"
echo
echo "Then restart Codex. If Codex asks you to review/trust command hooks, open /hooks and approve Lingling."
