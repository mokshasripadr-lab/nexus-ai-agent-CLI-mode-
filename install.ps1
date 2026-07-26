# ============================================================================
#  Nexus CLI — one-command installer  (Windows PowerShell)
#
#  Installs the whole Nexus agent into %USERPROFILE%\.nexus\agent and creates a
#  `nexus` command. Your API key is NEVER bundled — you paste it on first run
#  and it's saved privately to %USERPROFILE%\.nexus\config.json.
#
#  ONE-PASTE (paste into PowerShell):
#    irm https://raw.githubusercontent.com/mokshasripadr-lab/nexus-ai-agent-CLI-mode-/main/install.ps1 | iex
# ============================================================================
$ErrorActionPreference = "Stop"

$RepoRaw = "https://raw.githubusercontent.com/mokshasripadr-lab/nexus-ai-agent-CLI-mode-/main"
$Dest    = "$env:USERPROFILE\.nexus\agent"
$Bin     = "$env:USERPROFILE\.nexus\bin"

$Files = @(
  "nexus_cli.py","agent.py","tools.py","subagents.py","memory.py","improve.py",
  "run_scheduled.py","approve.py","look.py","atlas_mode.py","computer_use.py",
  "config.yaml","PRD.md","README.md"
)

Write-Host ""
Write-Host "  Installing Nexus CLI -> $Dest"
New-Item -ItemType Directory -Force -Path $Dest, $Bin | Out-Null

foreach ($f in $Files) {
  try   { Invoke-WebRequest -UseBasicParsing "$RepoRaw/$f" -OutFile "$Dest\$f" }
  catch { Write-Host "    (skipped $f)" }
}
Remove-Item "$Dest\.env" -ErrorAction SilentlyContinue

# nexus.cmd launcher
"@echo off`r`npython `"$Dest\nexus_cli.py`" %*" | Set-Content -Encoding ASCII "$Bin\nexus.cmd"

# Add bin to the user PATH once.
$userPath = [Environment]::GetEnvironmentVariable("Path","User")
if ($userPath -notlike "*$Bin*") {
  [Environment]::SetEnvironmentVariable("Path", "$userPath;$Bin", "User")
}
$env:Path = "$env:Path;$Bin"

Write-Host ""
Write-Host "  Installed! Starting Nexus now - pick a model and paste your key."
Write-Host ""
python "$Dest\nexus_cli.py"
