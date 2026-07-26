
#!/usr/bin/env bash
# Nexus CLI installer  (macOS & Linux)
# Installs the agent, makes a `nexus` command, and starts it.
# Your API key is never included — you pick a model and paste your key on first run.

set -e

REPO="https://raw.githubusercontent.com/mokshasripadr-lab/nexus-ai-agent-CLI-mode-/main"
AGENT="$HOME/.nexus/agent"
BIN="$HOME/.local/bin"

FILES="nexus_cli.py agent.py tools.py subagents.py memory.py improve.py run_scheduled.py approve.py look.py atlas_mode.py computer_use.py config.yaml PRD.md README.md"

echo "Installing Nexus CLI..."
mkdir -p "$AGENT" "$BIN"

# Copy the files if you're running this from the folder, otherwise download them.
HERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
for f in $FILES; do
  if [ -f "$HERE/$f" ]; then
    cp "$HERE/$f" "$AGENT/$f"
  else
    curl -fsSL "$REPO/$f" -o "$AGENT/$f"
  fi
done

# Make the `nexus` command.
echo '#!/usr/bin/env bash'                         >  "$BIN/nexus"
echo "exec python3 \"$AGENT/nexus_cli.py\" \"\$@\"" >> "$BIN/nexus"
chmod +x "$BIN/nexus"

# Remember `nexus` for future terminals.
grep -q '.local/bin' "$HOME/.zshrc"  2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"
grep -q '.local/bin' "$HOME/.bashrc" 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"

echo ""
echo "Installed! Starting Nexus now."
echo "Next time, just type:  nexus"
echo ""

# Start it, reading your keyboard from the terminal (works even via curl | bash).
python3 "$AGENT/nexus_cli.py" < /dev/tty
