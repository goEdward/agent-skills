$ErrorActionPreference = "Stop"

# 在 Windows 普通用户会话中独立后台常驻启动 cdp-proxy.mjs（无黑框），
# 彻底避免 Codex / Antigravity 命令执行器退出时随 Job Object 终止后台进程导致重复弹出 CDP 授权。

$SkillDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ProxyScript = Join-Path $SkillDir "scripts\cdp-proxy.mjs"
$CheckDepsScript = Join-Path $SkillDir "scripts\check-deps.mjs"
$LogFile = Join-Path $env:TEMP "cdp-proxy.log"
$NodeExe = (Get-Command node -ErrorAction Stop).Source

$existing = Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
  Where-Object { $_.CommandLine -like "*cdp-proxy.mjs*" }

if ($existing) {
  Write-Host "cdp-proxy 已在运行 (PID: $($existing[0].ProcessId))，直接验证连接状态..."
} else {
  Write-Host "正在 Windows 独立后台启动 cdp-proxy.mjs（日志：$LogFile）..."
  Start-Process -FilePath $NodeExe `
    -ArgumentList @($ProxyScript, "--log-file", $LogFile) `
    -WindowStyle Hidden
  Start-Sleep -Milliseconds 800
}

Write-Host "执行一次连接检查（如 Chrome 弹出远程调试授权框，请在 25 秒内点击允许）..."
& $NodeExe $CheckDepsScript --quick
Write-Host "完成。保持浏览器与本常驻进程运行，后续 AI 任务将复用同一条 CDP 连接，不再重复弹窗。"
