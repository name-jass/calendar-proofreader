# 把仓库里的引擎同步到 Skill 的 engine/，保证"一套代码两个入口"。
# 用法：
#   pwsh sync_engine.ps1 -Project <仓库根目录>
# 方向：仓库 -> Skill（仓库里那份是权威，避免 Skill 反向覆盖）

param(
    [Parameter(Mandatory = $true)][string]$Project,
    [string]$Skill = (Join-Path $PSScriptRoot "..")
)

$src = Join-Path $Project "calproof"
$dst = Join-Path $Skill   "engine\calproof"

if (-not (Test-Path -LiteralPath $src)) {
    "找不到项目引擎：$src"
    exit 1
}

if (Test-Path -LiteralPath $dst) {
    Remove-Item -LiteralPath $dst -Recurse -Force
}
New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force

Get-ChildItem -LiteralPath $dst -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force

$files = @(Get-ChildItem -LiteralPath (Join-Path $dst "calproof") -File -Filter "*.py" -ErrorAction SilentlyContinue)
if ($files.Count -eq 0) { $files = @(Get-ChildItem -LiteralPath $dst -File -Filter "*.py") }
"已同步引擎：$($files.Count) 个 .py 文件"

# ---- 版式档案（profiles）双向合并：仓库与 Skill 都可能产生新档案 ----
$pProj = Join-Path $Project "profiles"
$pSkill = Join-Path $Skill  "profiles"
New-Item -ItemType Directory -Path $pProj  -Force | Out-Null
New-Item -ItemType Directory -Path $pSkill -Force | Out-Null

$merged = 0
foreach ($f in (Get-ChildItem -LiteralPath $pProj -File -Filter "*.json" -ErrorAction SilentlyContinue)) {
    $t = Join-Path $pSkill $f.Name
    if (-not (Test-Path -LiteralPath $t) -or (Get-Item -LiteralPath $f.FullName).LastWriteTimeUtc -gt (Get-Item -LiteralPath $t).LastWriteTimeUtc) {
        Copy-Item -LiteralPath $f.FullName -Destination $t -Force
        $merged++
    }
}
foreach ($f in (Get-ChildItem -LiteralPath $pSkill -File -Filter "*.json" -ErrorAction SilentlyContinue)) {
    $t = Join-Path $pProj $f.Name
    if (-not (Test-Path -LiteralPath $t) -or (Get-Item -LiteralPath $f.FullName).LastWriteTimeUtc -gt (Get-Item -LiteralPath $t).LastWriteTimeUtc) {
        Copy-Item -LiteralPath $f.FullName -Destination $t -Force
        $merged++
    }
}
$nProj  = @(Get-ChildItem -LiteralPath $pProj  -File -Filter "*.json" -ErrorAction SilentlyContinue).Count
$nSkill = @(Get-ChildItem -LiteralPath $pSkill -File -Filter "*.json" -ErrorAction SilentlyContinue).Count
"已合并版式档案：$merged 个文件（仓库 $nProj 个 / Skill $nSkill 个）"
