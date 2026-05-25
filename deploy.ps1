# Windows PowerShell 自动部署脚本
# 作用：将本地开发的 localsubdownloader 插件代码一键同步发布到群晖 SA6400 NAS 共享路径中
$ErrorActionPreference = "Stop"

# 使用原生相对路径，拥有 100% 环境兼容性
$SourceDir = "plugins.v2\localsubdownloader"
$DestDir = "\\sa6400\docker\moviepilot\config\local_plugins\plugins.v2\localsubdownloader"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "[LocalSubDownloader] Start deployment..." -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# 1. 检查本地源目录是否存在
if (-not (Test-Path $SourceDir)) {
    Write-Host "Error: Local source directory does not exist: $SourceDir" -ForegroundColor Red
    exit 1
}

# 2. 检查群晖网络共享路径是否可访问
$DestParent = Split-Path $DestDir -Parent
if (-not (Test-Path $DestParent)) {
    Write-Host "Error: Cannot access NAS path: $DestParent" -ForegroundColor Red
    Write-Host "Please ensure SA6400 NAS is connected." -ForegroundColor Yellow
    exit 1
}

# 3. 确保目标插件文件夹存在，若不存在则自动创建
if (-not (Test-Path $DestDir)) {
    New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
    Write-Host "Created destination directory on NAS." -ForegroundColor Gray
}

# 4. 执行文件拷贝同步
Write-Host "Copying files to $DestDir ..." -ForegroundColor Gray
Copy-Item -Path "$SourceDir\*" -Destination $DestDir -Recurse -Force

Write-Host "=========================================" -ForegroundColor Green
Write-Host "Deployment completed successfully!" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Green
