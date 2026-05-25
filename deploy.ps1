# Windows PowerShell 自动部署脚本
# 作用：将本地开发的 localsubdownloader 插件代码一键同步发布到群晖 SA6400 NAS 共享路径中
$ErrorActionPreference = "Stop"

# 获取脚本所在的根目录
$SourceDir = Join-Path $PSScriptRoot "plugins.v2\localsubdownloader"
$DestDir = "\\sa6400\docker\moviepilot\config\local_plugins\plugins.v2\localsubdownloader"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "🚀 开始部署 LocalSubDownloader 插件到群晖 NAS..." -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# 1. 检查本地源目录是否存在
if (-not (Test-Path $SourceDir)) {
    Write-Host "❌ 错误：本地源目录不存在: $SourceDir" -ForegroundColor Red
    exit 1
}

# 2. 检查群晖网络共享路径是否可访问
$DestParent = Split-Path $DestDir -Parent
if (-not (Test-Path $DestParent)) {
    Write-Host "❌ 错误：无法访问群晖 NAS 共享路径: $DestParent" -ForegroundColor Red
    Write-Host "💡 提示：请确保群晖 SA6400 NAS 已开机，且当前 Win11 电脑已在资源管理器中成功建立该共享文件夹的网络连接。" -ForegroundColor Yellow
    exit 1
}

# 3. 确保目标插件文件夹存在，若不存在则自动创建
if (-not (Test-Path $DestDir)) {
    New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
    Write-Host "📁 目标目录不存在，已成功在 NAS 上自动创建。" -ForegroundColor Gray
}

# 4. 执行文件拷贝同步
Write-Host "📦 正在复制本地代码至: $DestDir ..." -ForegroundColor Gray
Copy-Item -Path "$SourceDir\*" -Destination $DestDir -Recurse -Force

Write-Host "=========================================" -ForegroundColor Green
Write-Host "🎉 部署同步成功！请在 MoviePilot 插件页面中刷新或重启容器以使新版生效。" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Green
