#!/usr/bin/env bash
# run.command — 美股概念板块实时行情看板启动脚本（macOS）
# 使用 caffeinate 防止 Mac 在运行期间进入睡眠

set -euo pipefail

# 切换到脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 加载 .env 环境变量
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

# 浏览器用本机地址打开（避免走网络）
LOCAL_URL="http://127.0.0.1:${PORT}"

# 提示信息
echo "=========================================="
echo "  美股概念板块实时行情看板"
echo "  本机访问: $LOCAL_URL"
if [[ "$HOST" == "0.0.0.0" ]]; then
    LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "<本机IP>")
    echo "  局域网访问: http://${LAN_IP}:${PORT}"
fi
echo "  按 Ctrl+C 停止服务"
echo "=========================================="
echo ""

# 打开浏览器（等待服务启动后）
open_browser() {
    sleep 2
    open "$LOCAL_URL"
}

# 后台打开浏览器
open_browser &

# 用 caffeinate 保持系统唤醒，启动 uvicorn
# --reload：改后端 .py 代码保存后自动重载，无需重启（重载瞬间 tick 流断开，几秒后自动重连）
caffeinate -i \
    "$SCRIPT_DIR/.venv/bin/uvicorn" \
    app.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --reload
