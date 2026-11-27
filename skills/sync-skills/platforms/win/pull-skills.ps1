$ErrorActionPreference = "Stop"

# Automatically resolve repository root from this script's location
$DefaultRepo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
$RepoDir = if ($env:SKILLS_REPO_DIR) { $env:SKILLS_REPO_DIR } else { $DefaultRepo }

Write-Host "==> Pulling latest skills in: $RepoDir"
python "$RepoDir\skills\sync-skills\scripts\sync_tool.py" pull --repo "$RepoDir" --refresh-setup
Write-Host "==> Done."
