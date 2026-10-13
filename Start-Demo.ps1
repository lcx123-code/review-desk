$ErrorActionPreference = 'Stop'
$demoScript = Join-Path $PSScriptRoot 'plugins\review-desk\scripts\demo.py'
$demoWorkspace = Join-Path $PSScriptRoot 'demo-work'
Write-Host '正在启动 Review Desk 演示。打开下方链接即可审查代码和运行效果。按 Ctrl+C 停止。'
& python $demoScript --workspace $demoWorkspace
