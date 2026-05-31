#!/usr/bin/env bash
# 一键安装脚本：创建 venv、安装依赖、初始化数据库
# 用法：bash scripts/install.sh [--skip-network-check]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"
PYTHON_BIN=""
SKIP_NETWORK=false

# ── 参数解析 ──────────────────────────────────────────────────────────────────
for arg in "$@"; do
  case $arg in
    --skip-network-check) SKIP_NETWORK=true ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

# ── 颜色输出 ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; exit 1; }

echo "=== 投资系统一键安装 ==="
echo "项目根目录: $PROJECT_ROOT"
cd "$PROJECT_ROOT"

# ── 1. Python 版本检查 ────────────────────────────────────────────────────────
echo ""
echo "── 步骤 1/5: 检查 Python 版本 ──"
for candidate in python3.11 python3 python; do
  if command -v "$candidate" &>/dev/null; then
    ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; then
      PYTHON_BIN="$candidate"
      ok "找到 Python $ver ($candidate)"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  fail "未找到 Python 3.11+。请先安装：brew install python@3.11 或访问 https://python.org"
fi

# ── 2. 创建虚拟环境 ───────────────────────────────────────────────────────────
echo ""
echo "── 步骤 2/5: 创建虚拟环境 ──"
if [ -d "$VENV_DIR" ]; then
  warn "虚拟环境已存在，跳过创建（如需重建请先删除 .venv/）"
else
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  ok "虚拟环境已创建：$VENV_DIR"
fi

PY="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"
INV="$VENV_DIR/bin/inv"

# ── 3. 安装依赖 ───────────────────────────────────────────────────────────────
echo ""
echo "── 步骤 3/5: 安装项目依赖 ──"
"$PIP" install --quiet --upgrade pip
"$PIP" install --quiet -e ".[dev]"
ok "依赖安装完成"

# ── 4. 网络可达性检查 ─────────────────────────────────────────────────────────
echo ""
echo "── 步骤 4/5: 检查网络可达性 ──"
if [ "$SKIP_NETWORK" = true ]; then
  warn "已跳过网络检查（--skip-network-check）"
else
  # 检查腾讯行情接口（inv snapshot pull 依赖）
  if curl -sf --max-time 5 "https://qt.gtimg.cn/q=sh000001" -o /dev/null 2>/dev/null; then
    ok "腾讯行情接口可达（qt.gtimg.cn）"
  else
    warn "腾讯行情接口不可达（qt.gtimg.cn）。snapshot pull 命令将无法获取实时行情，其他功能不受影响。"
  fi

  # 检查 Anthropic API（causal/llm 功能依赖）
  if [ -f "$PROJECT_ROOT/.env" ] && grep -q "ANTHROPIC_API_KEY" "$PROJECT_ROOT/.env" 2>/dev/null; then
    ok "检测到 .env 中的 ANTHROPIC_API_KEY 配置"
  else
    warn "未检测到 ANTHROPIC_API_KEY。因果推理和 AI Skills 功能需要此密钥，请在 .env 文件中配置。"
  fi
fi

# ── 5. 初始化数据库 ───────────────────────────────────────────────────────────
echo ""
echo "── 步骤 5/5: 初始化数据库 ──"
DB_PATH="$PROJECT_ROOT/data/portfolio.db"
if [ -f "$DB_PATH" ]; then
  warn "数据库已存在（$DB_PATH），跳过初始化。如需重建请先备份并删除该文件。"
else
  mkdir -p "$PROJECT_ROOT/data"
  "$INV" migrate run
  ok "数据库初始化完成：$DB_PATH"
fi

# ── 完成 ──────────────────────────────────────────────────────────────────────
echo ""
echo "=== 安装完成 ==="
"$INV" version
echo ""
echo "常用命令："
echo "  .venv/bin/inv snapshot pull          # 拉取今日行情"
echo "  .venv/bin/inv dashboard render       # 生成战情室"
echo "  .venv/bin/python -m pytest -q        # 运行测试"
echo ""
echo "如遇问题，请查看 docs/baseline-snapshot.md 中的基线状态。"
