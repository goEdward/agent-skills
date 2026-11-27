$ErrorActionPreference = "Stop"

$SkillDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$MaintScript = Join-Path $SkillDir "scripts\proxy-maintenance.mjs"
$NodeExe = (Get-Command node -ErrorAction Stop).Source

& $NodeExe $MaintScript stop
