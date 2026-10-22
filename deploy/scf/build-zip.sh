#!/usr/bin/env bash
# ============================================================================
# 腾讯云 SCF 代码包构建（Linux / WSL 备用版；Windows 请用 build-zip.ps1）
#
# 关键点：本机 pip install 会装出 Windows 二进制（*.pyd / *.dll），
# 上传到 Linux 运行时后 import 直接报 "invalid ELF header"。
# 所以必须用 --platform manylinux 交叉安装，且 --only-binary=:all: 禁止源码编译。
#
# 用法：
#   ./deploy/scf/build-zip.sh                      # 默认 PYTHON_VERSION=3.10
#   PYTHON_VERSION=3.9 ./deploy/scf/build-zip.sh
#   ./deploy/scf/build-zip.sh --dry-run            # 只列清单，不动任何文件
#   ./deploy/scf/build-zip.sh --skip-deps          # 复用已有 build/pkg（离线调试用）
# ============================================================================
set -euo pipefail

PYTHON_VERSION="${PYTHON_VERSION:-3.10}"
OUTPUT="${OUTPUT:-}"
PYTHON="${PYTHON:-python3}"
PLATFORMS="${PLATFORMS:-manylinux2014_x86_64 manylinux_2_17_x86_64 manylinux_2_28_x86_64}"
DRY_RUN=0
SKIP_DEPS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip-deps) SKIP_DEPS=1 ;;
    --python-version) PYTHON_VERSION="$2"; shift ;;
    --output) OUTPUT="$2"; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
PKG_DIR="$BUILD_DIR/pkg"
[ -n "$OUTPUT" ] || OUTPUT="$REPO_ROOT/dist/scf-biotriage-py${PYTHON_VERSION//./}.zip"

INCLUDE_FILES="api.py account.html index.html knowledge_base.json precomputed_ai.json requirements.txt documents.json"
INCLUDE_DIRS="accounts assets"
EXCLUDES='-x */__pycache__/* -x */.venv/* -x *.db -x *.pyc -x *.db-journal -x pkg/bin/* -x *.exe'

# 目标版本补丁依赖（不写进 requirements.txt：Python 3.11+ 不需要它）。
# pip 的 --platform/--python-version 只影响 wheel 标签与下载，**依赖解析时的环境标记仍按运行中的
# 解释器评估**（本机可能是 3.12/3.14），于是 python_version < "3.11" 被判为假，
# exceptiongroup 被静默跳过；线上是 3.10 -> import 失败。显式追加 + 依赖审计兜底。
EXTRA_TARGET_DEPS="exceptiongroup>=1.0.2"

echo "[scf] repo=$REPO_ROOT"
echo "[scf] python-version=$PYTHON_VERSION platforms=$PLATFORMS"
echo "[scf] output=$OUTPUT"

list_plan() {
  echo "[scf] 将要打包的源文件（不含 pkg/ 依赖树）："
  for f in $INCLUDE_FILES; do
    [ -f "$REPO_ROOT/$f" ] && echo "  + $f"
  done
  for d in $INCLUDE_DIRS; do
    [ -d "$REPO_ROOT/$d" ] && find "$REPO_ROOT/$d" -type f \
      -not -path '*/__pycache__/*' -not -name '*.pyc' -not -name '*.db' \
      | sed "s|^$REPO_ROOT/|  + |" | sort
  done
  echo "  + scf_bootstrap  (deploy/scf/scf_bootstrap)"
  echo "[scf] 依赖目录: pkg/（pip --platform ... --only-binary=:all: 交叉安装，条目不逐个列出）"
}

if [ "$DRY_RUN" = "1" ]; then
  list_plan
  echo "[scf] DRY-RUN：未创建 build/、未安装依赖、未生成 zip"
  exit 0
fi

if [ "$SKIP_DEPS" = "1" ] && [ -d "$PKG_DIR" ]; then
  # 复用依赖时只清业务文件，保住 build/pkg
  find "$BUILD_DIR" -mindepth 1 -maxdepth 1 ! -name pkg -exec rm -rf {} +
else
  rm -rf "$BUILD_DIR"
fi
mkdir -p "$BUILD_DIR"

echo "[scf] 复制业务文件 -> $BUILD_DIR"
for f in $INCLUDE_FILES; do
  [ -f "$REPO_ROOT/$f" ] && cp "$REPO_ROOT/$f" "$BUILD_DIR/"
done
for d in $INCLUDE_DIRS; do
  [ -d "$REPO_ROOT/$d" ] && cp -r "$REPO_ROOT/$d" "$BUILD_DIR/"
done
cp "$SCRIPT_DIR/scf_bootstrap" "$BUILD_DIR/scf_bootstrap"
chmod +x "$BUILD_DIR/scf_bootstrap"
find "$BUILD_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -name '*.pyc' -delete 2>/dev/null || true
find "$BUILD_DIR" -name '*.db' -delete 2>/dev/null || true

if [ "$SKIP_DEPS" = "1" ]; then
  echo "[scf] --skip-deps：复用已有 pkg/（不会重新下载）"
  [ -d "$PKG_DIR" ] || { echo "[scf] ✗ --skip-deps 但 build/pkg 不存在，请去掉该开关重建" >&2; exit 1; }
else
  echo "[scf] 交叉安装依赖 -> $PKG_DIR"
  PLATFORM_ARGS=""
  for p in $PLATFORMS; do PLATFORM_ARGS="$PLATFORM_ARGS --platform $p"; done
  if ! "$PYTHON" -m pip install --target "$PKG_DIR" --upgrade --no-compile \
      --implementation cp --only-binary=:all: --python-version "$PYTHON_VERSION" \
      $PLATFORM_ARGS -r "$REPO_ROOT/requirements.txt" "$EXTRA_TARGET_DEPS"; then
    echo "[scf] ✗ 交叉安装失败。请检查：① 能否访问 PyPI；② 有无该平台 wheel" >&2
    echo "        （可把更多平台加进 PLATFORMS，例如 manylinux_2_34_x86_64）；" >&2
    echo "        ③ 不要去掉 --only-binary=:all:（源码编译会产出 Windows 二进制）。" >&2
    echo "        构建已中止，未生成 zip。" >&2
    exit 1
  fi
fi

# 兜底检查：不能出现 Windows 原生库
if find "$PKG_DIR" \( -name '*.pyd' -o -name '*.dll' -o -name '*.lib' \) 2>/dev/null | grep -q .; then
  echo "[scf] ✗ pkg/ 里发现 Windows 原生库（*.pyd/*.dll），上传后必然 invalid ELF header" >&2
  find "$PKG_DIR" \( -name '*.pyd' -o -name '*.dll' -o -name '*.lib' \) | head -10 >&2
  exit 1
fi

# ---------- 依赖审计：条件依赖 + 安装完整性 ----------
# pip 的 --platform / --python-version 只影响 wheel 标签与下载，依赖解析仍按"运行中的解释器"
# 评估环境标记，于是 python_version < "3.11" 这类条件依赖会被静默跳过（线上 3.10 就 import 失败）。
echo
echo "[scf] 依赖审计（条件依赖 + 安装完整性）"
if ! PYTHONIOENCODING=utf-8 "$PYTHON" - "$PKG_DIR" "$PYTHON_VERSION" <<'SCF_AUDIT_PY'
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
SCF_AUDIT_PY
then
  echo "[scf] ✗ 依赖审计未通过：见上面缺失条目。构建已中止，未生成 zip。" >&2
  exit 1
fi

# 兜底检查：scf_bootstrap 必须是 LF
if grep -q $'\r' "$BUILD_DIR/scf_bootstrap"; then
  echo "[scf] ✗ scf_bootstrap 含 CRLF 行尾，云函数里会执行失败；请转成 LF" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT")"
echo "[scf] 打 zip -> $OUTPUT"
command -v zip >/dev/null || { echo "[scf] ✗ 缺少 zip 命令，请安装 zip 或改用 Windows 版脚本" >&2; exit 1; }
( cd "$BUILD_DIR" && zip -rqX "$OUTPUT" . $EXCLUDES )

SIZE=$(wc -c < "$OUTPUT")
echo "[scf] ✓ 完成：$OUTPUT"
echo "[scf] 包大小：$SIZE 字节（约 $((SIZE/1024/1024)) MB）"
echo "[scf] 体积上限与是否改用「层」见 deploy/scf/README.md（以腾讯云当期文档为准）"
