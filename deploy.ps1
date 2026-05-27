# Windows PowerShell 自动部署脚本
# 作用：将本地开发的 MoviePilot 插件与配置一键同步发布到群晖 SA6400 NAS 共享路径中
$ErrorActionPreference = "Stop"

# 使用原生相对路径，拥有 100% 环境兼容性
$SourceRoot = "plugins.v2"
$DestRoot = "\\sa6400\docker\moviepilot\config\local_plugins"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "[MoviePilot Plugins] Start deployment..." -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# 1. 检查本地源目录与 package.v2.json 是否存在
if (-not (Test-Path $SourceRoot)) {
    Write-Host "Error: Local source directory does not exist: $SourceRoot" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path "package.v2.json")) {
    Write-Host "Error: Local package.v2.json does not exist." -ForegroundColor Red
    exit 1
}

# 2. 检查群晖网络共享路径是否可访问
if (-not (Test-Path $DestRoot)) {
    Write-Host "Error: Cannot access NAS path: $DestRoot" -ForegroundColor Red
    Write-Host "Please ensure SA6400 NAS is connected and path exists." -ForegroundColor Yellow
    exit 1
}

# 3. 部署 package.v2.json 配置文件
Write-Host "Copying package.v2.json to $DestRoot ..." -ForegroundColor Gray
Copy-Item -Path "package.v2.json" -Destination "$DestRoot\package.v2.json" -Force

# 4. 确保目标 plugins.v2 文件夹存在
$DestPluginsDir = "$DestRoot\plugins.v2"
if (-not (Test-Path $DestPluginsDir)) {
    New-Item -ItemType Directory -Path $DestPluginsDir -Force | Out-Null
    Write-Host "Created plugins.v2 directory on NAS." -ForegroundColor Gray
}

# 5. 执行插件文件夹拷贝同步
# 本地轻量字幕下载器 (LocalSubDownloader)
$SourceSub = "$SourceRoot\localsubdownloader"
$DestSub = "$DestPluginsDir\localsubdownloader"
if (Test-Path $SourceSub) {
    if (-not (Test-Path $DestSub)) {
        New-Item -ItemType Directory -Path $DestSub -Force | Out-Null
    }
    Write-Host "Copying LocalSubDownloader files to $DestSub ..." -ForegroundColor Gray
    Copy-Item -Path "$SourceSub\*" -Destination $DestSub -Recurse -Force
}

# 环境变量编辑器 (EnvEditor)
$SourceEnv = "$SourceRoot\enveditor"
$DestEnv = "$DestPluginsDir\enveditor"
if (Test-Path $SourceEnv) {
    if (-not (Test-Path $DestEnv)) {
        New-Item -ItemType Directory -Path $DestEnv -Force | Out-Null
    }
    Write-Host "Copying EnvEditor files to $DestEnv ..." -ForegroundColor Gray
    Copy-Item -Path "$SourceEnv\*" -Destination $DestEnv -Recurse -Force
}

Write-Host "=========================================" -ForegroundColor Green
Write-Host "Deployment completed successfully!" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Green
