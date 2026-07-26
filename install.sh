#!/usr/bin/env bash
# ============================================================================
#  Nexus CLI — one-command installer  (macOS + Linux)
#
#  Installs the WHOLE Nexus agent (chat + autonomous /do crew + /cron
#  scheduler + macOS computer-use) into ~/.nexus/agent and creates a global
#  `nexus` command. Your API key is NEVER bundled — you paste it on first run
#  and it's saved privately to ~/.nexus/config.json (chmod 600).
#
#  ONE-PASTE (from anywhere):
#    curl -fsSL https://raw.githubusercontent.com/mokshasripadr-lab/nexus-ai-agent-CLI-mode-/main/install.sh | bash
#
#  Or run this file directly from the atlas-agent folder:  bash install.sh
# ============================================================================
set -e

REPO_RAW="https://raw.githubusercontent.com/mokshasripadr-lab/nexus-ai-agent-CLI-mode-/main"
DEST="$HOME/.nexus/agent"
BIN="$HOME/.local/bin"

# Every backend file the agent needs to run — but NOT .env / keys.
FILES=(
  nexus_cli.py agent.py tools.py subagents.py memory.py improve.py
  run_scheduled.py approve.py look.py atlas_mode.py computer_use.py
  config.yaml PRD.md README.md
)

echo ""
echo "  ✦ Installing Nexus CLI → $DEST"
mkdir -p "$DEST" "$BIN"

# Are we running next to the source files? (local install) or piped? (download)
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo '')"
LOCAL=0
[ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/nexus_cli.py" ] && LOCAL=1

for f in "${FILES[@]}"; do
  if [ "$LOCAL" = "1" ] && [ -f "$SRC_DIR/$f" ]; then
    cp "$SRC_DIR/$f" "$DEST/$f"
  else
    curl -fsSL "$REPO_RAW/$f" -o "$DEST/$f" 2>/dev/null || echo "    (skipped $f — not in repo)"
  fi
done

# Never carry over secrets.
rm -f "$DEST/.env" 2>/dev/null || true

# Create the global `nexus` launcher.
cat > "$BIN/nexus" <<EOF
#!/usr/bin/env bash
exec /usr/bin/env python3 "$DEST/nexus_cli.py" "\$@"
EOF
chmod +x "$BIN/nexus"

# Put ~/.local/bin on PATH for both zsh and bash, once.
for RC in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [ -f "$RC" ] || touch "$RC"
  grep -q 'HOME/.local/bin' "$RC" 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$RC"
done
export PATH="$HOME/.local/bin:$PATH"

# Optional: macOS computer-use extras (safe to skip / fail).
if [ "$(uname)" = "Darwin" ]; then
  python3 -m pip install --quiet --user pyobjc pynput >/dev/null 2>&1 || true
fi

echo ""
echo "  ✅ Installed!  Starting Nexus now — pick a model and paste your key."
echo ""
exec "$BIN/nexus"
