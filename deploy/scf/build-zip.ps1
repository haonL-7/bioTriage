<#
.SYNOPSIS
    腾讯云 SCF Web 函数代码包构建（Windows 主用；Linux/WSL 用同目录 build-zip.sh）

.DESCRIPTION
    产出可直接在腾讯云云函数控制台上传的 zip。
    关键点：Windows 本机 pip install 会装出 Windows 二进制（*.pyd / *.dll），
    上传到 Linux 运行时后 import 直接报 "invalid ELF header"。
    因此依赖一律用 --platform manylinux* --only-binary=:all: 交叉安装到 build/pkg，
    本机不需要 WSL，也不需要 Docker。

.PARAMETER PythonVersion
    目标运行时 Python 版本，必须与控制台里函数的运行时版本一致（默认 3.10）。

.PARAMETER DryRun
    只列出将要打进包的文件清单与将执行的 pip 命令，不动 build/、不装依赖、不生成 zip。

.PARAMETER SkipDeps
    跳过依赖安装，复用已有 build/pkg（离线调试）。

.EXAMPLE
    pwsh -File deploy/scf/build-zip.ps1 -DryRun
    pwsh -File deploy/scf/build-zip.ps1 -PythonVersion 3.10
#>
[CmdletBinding()]
param(
    [string]$PythonVersion = '3.10',
    [string]$Output = '',
    [string]$Python = 'python',
    [string[]]$Platforms = @('manylinux2014_x86_64', 'manylinux_2_17_x86_64', 'manylinux_2_28_x86_64'),
    [int]$SizeWarnMb = 40,
    [switch]$DryRun,
    [switch]$SkipDeps
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$BuildDir = Join-Path $ScriptDir 'build'
$PkgDir = Join-Path $BuildDir 'pkg'
if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path (Join-Path $RepoRoot 'dist') ('scf-biotriage-py{0}.zip' -f ($PythonVersion -replace '\.', ''))
}

# api.py 运行时要读的同目录文件（index.html 缺了 / 只能返回 JSON 兜底）
$IncludeFiles = @('api.py', 'account.html', 'index.html', 'knowledge_base.json', 'precomputed_ai.json', 'requirements.txt')
$IncludeDirs = @('accounts', 'assets')
$ExcludeDirs = @('__pycache__', '.venv', '.git', 'tests', 'deploy')
$ExcludeExt = @('.pyc', '.db', '.db-journal', '.db-wal', '.db-shm', '.pyd', '.dll', '.pdb', '.exe')

# 目标版本补丁依赖（不写进 requirements.txt：应用在 Python 3.11+ 并不需要它们）。
# 原因：pip 的 --platform/--python-version 只影响 wheel 兼容性标签与下载，
# **依赖解析阶段的环境标记仍按运行中的解释器评估**（本机可能是 3.12/3.14），
# 于是 python_version < "3.11" 被判为假，exceptiongroup 这类"低版本才需要"的包会被静默跳过，
# 而线上运行时是 3.10 -> import 直接 ModuleNotFoundError。这里显式补上，并由下面的依赖审计兜底。
$TargetPythonExtraDeps = @('exceptiongroup>=1.0.2')

function Test-Excluded([string]$Relative) {
    # pkg/bin 是 pip 生成的命令包装器（Windows 上是 .exe 启动器）；我们用 python -m uvicorn 启动，不需要
    if ($Relative -like 'pkg/bin/*') { return $true }
    foreach ($part in ($Relative -split '[\\/]')) {
        if ($ExcludeDirs -contains $part) { return $true }
    }
    foreach ($ext in $ExcludeExt) {
        if ($Relative.EndsWith($ext, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Get-PackageItems {
    $items = New-Object System.Collections.Generic.List[object]
    foreach ($name in $IncludeFiles) {
        $src = Join-Path $RepoRoot $name
        if (Test-Path -LiteralPath $src -PathType Leaf) {
            $items.Add([pscustomobject]@{ Source = $src; Target = $name })
        }
    }
    foreach ($dirName in $IncludeDirs) {
        $dir = Join-Path $RepoRoot $dirName
        if (-not (Test-Path -LiteralPath $dir -PathType Container)) { continue }
        Get-ChildItem -LiteralPath $dir -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($RepoRoot.Length + 1) -replace '\\', '/'
            if (-not (Test-Excluded $rel)) {
                $items.Add([pscustomobject]@{ Source = $_.FullName; Target = $rel })
            }
        }
    }
    $bootstrap = Join-Path $ScriptDir 'scf_bootstrap'
    if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) {
        throw "[scf] 缺少 deploy/scf/scf_bootstrap，无法构建代码包"
    }
    $items.Add([pscustomobject]@{ Source = $bootstrap; Target = 'scf_bootstrap' })
    return $items
}

function Assert-LfLineEndings([string]$Path) {
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    for ($i = 0; $i -lt $bytes.Length - 1; $i++) {
        if ($bytes[$i] -eq 13 -and $bytes[$i + 1] -eq 10) {
            throw "[scf] 错误：$Path 含 CRLF 行尾，云函数里 scf_bootstrap 会执行失败，请存成 LF"
        }
    }
}

Write-Host "[scf] repo        = $RepoRoot"
Write-Host "[scf] build dir   = $BuildDir"
Write-Host "[scf] python ver  = $PythonVersion (交叉安装的 wheel ABI 必须与函数运行时一致)"
Write-Host "[scf] platforms   = $($Platforms -join ', ')"
Write-Host "[scf] output      = $Output"

$items = Get-PackageItems
Write-Host ""
Write-Host ("[scf] 将要打进包的源文件（共 {0} 个；pkg/ 依赖树不逐条列）:" -f $items.Count)
foreach ($item in $items) { Write-Host ("  + {0}" -f $item.Target) }

$pipArgs = @('-m', 'pip', 'install', '--target', $PkgDir, '--upgrade', '--no-compile',
    '--implementation', 'cp', '--only-binary=:all:', '--python-version', $PythonVersion)
foreach ($platform in $Platforms) { $pipArgs += @('--platform', $platform) }
$pipArgs += @('-r', (Join-Path $RepoRoot 'requirements.txt'))
foreach ($extra in $TargetPythonExtraDeps) { $pipArgs += @($extra) }
Write-Host ""
Write-Host "[scf] 依赖安装命令:"
Write-Host ("  {0} {1}" -f $Python, ($pipArgs -join ' '))

if ($DryRun) {
    Write-Host ""
    Write-Host "[scf] DRY-RUN：未清理 build/、未安装依赖、未生成 zip"
    exit 0
}

if ($SkipDeps -and (Test-Path -LiteralPath $PkgDir)) {
    # 复用依赖时只清业务文件，保住 build/pkg（离线/拆层场景）
    Get-ChildItem -LiteralPath $BuildDir -Force | Where-Object { $_.Name -ne 'pkg' } | Remove-Item -Recurse -Force
}
elseif (Test-Path -LiteralPath $BuildDir) {
    Remove-Item -LiteralPath $BuildDir -Recurse -Force
}
New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

Write-Host ""
Write-Host "[scf] 复制业务文件"
foreach ($item in $items) {
    $dst = Join-Path $BuildDir ($item.Target -replace '/', [System.IO.Path]::DirectorySeparatorChar)
    $dstDir = Split-Path -Parent $dst
    if (-not (Test-Path -LiteralPath $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }
    Copy-Item -LiteralPath $item.Source -Destination $dst -Force
}
$bootstrapDst = Join-Path $BuildDir 'scf_bootstrap'
Assert-LfLineEndings $bootstrapDst

if ($SkipDeps) {
    Write-Host "[scf] -SkipDeps：复用已有 pkg/"
    if (-not (Test-Path -LiteralPath $PkgDir)) {
        throw "[scf] 错误：-SkipDeps 但 build/pkg 不存在，请去掉该开关重新构建"
    }
}
else {
    Write-Host "[scf] 交叉安装依赖 -> build/pkg"
    & $Python @pipArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[scf] 错误：交叉安装失败（pip 退出码见上）。构建已中止，未生成 zip —— 不要上传不完整的包。"
        Write-Host "  1) 能否访问 PyPI（离线环境请先把 wheel 下载到本地，再用 --find-links）"
        Write-Host "  2) 该平台是否有 wheel：可把更多平台加进 -Platforms，例如 manylinux_2_34_x86_64"
        Write-Host "  3) 不要去掉 --only-binary=:all:（源码编译会产出 Windows 二进制，上传后 invalid ELF header）"
        throw "[scf] 交叉安装失败，exit code $LASTEXITCODE"
    }
}

$nativeArtifacts = Get-ChildItem -LiteralPath $PkgDir -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in @('.pyd', '.dll', '.lib') }
if ($nativeArtifacts) {
    $nativeArtifacts | Select-Object -First 10 | ForEach-Object { Write-Host ("    " + $_.FullName) }
    throw "[scf] 错误：pkg/ 里发现 Windows 原生库（*.pyd/*.dll/*.lib），上传后必然 invalid ELF header"
}

foreach ($required in @('fastapi', 'uvicorn', 'sqlalchemy', 'psycopg2')) {
    if (-not (Test-Path -LiteralPath (Join-Path $PkgDir $required))) {
        Write-Warning "[scf] pkg/ 里没有 $required：确认 requirements.txt 是否需要它（云端 import 会失败）"
    }
}

# ---------- 依赖审计：条件依赖 + 安装完整性 ----------
# pip 的 --platform / --python-version 只影响 wheel 标签与下载，依赖解析仍按"运行中的解释器"
# 评估环境标记，于是 python_version < "3.11" 这类条件依赖会被静默跳过（线上 3.10 就 import 失败）。
$AuditScript = @'
import re
import sys
from pathlib import Path

pkg_dir = Path(sys.argv[1])
target = sys.argv[2]
target_tuple = tuple(int(x) for x in re.findall(r"\d+", target)) or (3, 10)
full_tuple = target_tuple + (0,) * (3 - len(target_tuple))

MARKER_RE = re.compile(
    r"(python_full_version|python_version)\s*(<=|>=|==|!=|<|>)\s*[\"']([^\"']+)[\"']"
)


def _norm(value):
    return tuple(int(x) for x in re.findall(r"\d+", value)) or (0,)


def _compare(lhs, operator, rhs):
    a, b = _norm(str(lhs)), _norm(rhs)
    size = max(len(a), len(b))
    a = a + (0,) * (size - len(a))
    b = b + (0,) * (size - len(b))
    if operator == "<":
        return a < b
    if operator == "<=":
        return a <= b
    if operator == ">":
        return a > b
    if operator == ">=":
        return a >= b
    if operator == "==":
        return a == b
    if operator == "!=":
        return a != b
    return None


def evaluate(marker):
    """True/False = 在目标版本下成立/不成立；None = 本审计不判定的条件（平台类等）"""
    if "extra" in marker:
        return False
    results = []
    for or_part in re.split(r"\bor\b", marker):
        and_results = []
        for and_part in re.split(r"\band\b", or_part):
            found = MARKER_RE.search(and_part)
            if not found:
                return None
            subject, operator, rhs = found.group(1), found.group(2), found.group(3)
            lhs = full_tuple if subject == "python_full_version" else target_tuple
            outcome = _compare(lhs, operator, rhs)
            if outcome is None:
                return None
            and_results.append(outcome)
        results.append(all(and_results))
    return any(results)


def canon(name):
    return re.sub(r"[-_.]+", "-", name).lower()


installed = {}
for info in pkg_dir.glob("*.dist-info"):
    dist_name = info.name[: -len(".dist-info")].rsplit("-", 1)[0]
    installed.setdefault(canon(dist_name), info)

checked = 0
conditional = 0
applicable = 0
problems = []

for name, info in sorted(installed.items()):
    metadata = info / "METADATA"
    if not metadata.is_file():
        continue
    checked += 1
    for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        spec = line.split(":", 1)[1].strip()
        if ";" not in spec:
            continue
        requirement, marker = spec.split(";", 1)
        conditional += 1
        if evaluate(marker.strip()) is not True:
            continue
        applicable += 1
        dependency = re.split(r"[\s\[<>=!~;(]", requirement.strip(), 1)[0]
        if canon(dependency) not in installed:
            problems.append(
                "[scf] 缺失条件依赖：{0}（由 {1} 在 Python {2} 下要求）".format(
                    dependency, info.name, target
                )
            )
    record = info / "RECORD"
    if record.is_file():
        for entry in record.read_text(encoding="utf-8", errors="replace").splitlines():
            relative = entry.split(",", 1)[0].strip()
            if not relative or relative.endswith(".pyc"):
                continue
            if relative.startswith("..") or "/../" in relative:
                continue  # pip 把控制台脚本写在 target 之外（.../bin/*.exe），不属于包内容
            if not (pkg_dir / relative).exists():
                problems.append(
                    "[scf] 安装不完整：{0} 缺少文件 {1}".format(info.name, relative)
                )
                break

if problems:
    for problem in problems:
        print(problem)
    print(
        "[scf] 依赖审计失败：检查 {0} 个包，条件依赖 {1} 条，问题 {2} 条".format(
            checked, conditional, len(problems)
        )
    )
    sys.exit(1)

print(
    "[scf] 依赖审计通过：检查 {0} 个包，条件依赖 {1} 条，缺失 0（目标 Python {2}，其中成立 {3} 条）".format(
        checked, conditional, target, applicable
    )
)
'@
Write-Host ""
Write-Host "[scf] 依赖审计（条件依赖 + 安装完整性）"
$auditPath = Join-Path ([System.IO.Path]::GetTempPath()) ("scf_dep_audit_" + $PID + ".py")
[System.IO.File]::WriteAllText($auditPath, $AuditScript, (New-Object System.Text.UTF8Encoding($false)))
$previousPyIoEncoding = $env:PYTHONIOENCODING
$env:PYTHONIOENCODING = 'utf-8'   # 审计输出统一 UTF-8，日志采集/跨平台排查都不会乱码
try {
  & $Python $auditPath $PkgDir $PythonVersion
  $auditExit = $LASTEXITCODE
}
finally {
  $env:PYTHONIOENCODING = $previousPyIoEncoding
  Remove-Item -LiteralPath $auditPath -Force -ErrorAction SilentlyContinue
}
if ($auditExit -ne 0) {
  throw "[scf] 依赖审计未通过：见上面缺失条目。构建已中止，未生成 zip。"
}

Write-Host ""
Write-Host "[scf] 打 zip"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$outputDir = Split-Path -Parent $Output
if (-not (Test-Path -LiteralPath $outputDir)) { New-Item -ItemType Directory -Path $outputDir -Force | Out-Null }
if (Test-Path -LiteralPath $Output) { Remove-Item -LiteralPath $Output -Force }

$zip = [System.IO.Compression.ZipFile]::Open($Output, [System.IO.Compression.ZipArchiveMode]::Create)
$entryCount = 0
try {
    foreach ($file in (Get-ChildItem -LiteralPath $BuildDir -Recurse -File)) {
        $rel = $file.FullName.Substring($BuildDir.Length + 1) -replace '\\', '/'
        if (Test-Excluded $rel) { continue }
        $entry = $zip.CreateEntry($rel, [System.IO.Compression.CompressionLevel]::Optimal)
        $entry.LastWriteTime = $file.LastWriteTime
        if ($rel -eq 'scf_bootstrap') {
            # 保留 unix 可执行位 0755：Compress-Archive 会丢权限，云函数里 scf_bootstrap 就跑不起来
            $packed = [uint32]([uint32]33261 -shl 16)
            $entry.ExternalAttributes = [System.BitConverter]::ToInt32([System.BitConverter]::GetBytes($packed), 0)
        }
        $inputStream = [System.IO.File]::OpenRead($file.FullName)
        try {
            $stream = $entry.Open()
            try { $inputStream.CopyTo($stream) } finally { $stream.Dispose() }
        }
        finally { $inputStream.Dispose() }
        $entryCount++
    }
}
finally { $zip.Dispose() }

$sizeMb = [math]::Round((Get-Item -LiteralPath $Output).Length / 1MB, 2)
Write-Host ""
Write-Host ("[scf] 完成：{0}" -f $Output)
Write-Host ("[scf] 条目 {0} 个，体积 {1} MB" -f $entryCount, $sizeMb)
if ($sizeMb -gt $SizeWarnMb) {
    Write-Warning ("[scf] 体积 {0} MB 已超过提示阈值 {1} MB：可能触到代码包上限，请按 deploy/scf/README.md 改用层（具体限额以腾讯云当期文档为准）" -f $sizeMb, $SizeWarnMb)
}
Write-Host "[scf] 下一步：控制台上传 zip，并配置环境变量（清单见 deploy/scf/README.md）"
