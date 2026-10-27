#!/bin/bash
# ============================================================
# 逐个传输 mp3 文件到远程服务器，观察 SSH 链路复用效果
#
# 用法:
#   ./test_keep_control.sh [user]
#
#   默认 user=root，可指定其他用户:
#   ./test_keep_control.sh aigc
#
# 预期效果:
#   第 1 个文件: init_ok   → 建立 ControlMaster（慢，需要认证）
#   第 2+ 个文件: init_skip → 复用已有连接（快，跳过认证）
#   ControlMaster 在最后一次传输后 600s 自动清理
# ============================================================

set -euo pipefail

# 确保从任意目录运行都能找到 rsync_sync 模块
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="${PARENT_DIR}:${PYTHONPATH:-}"

USER="${1:-root}"
HOST="47.251.6.222"
PORT="14922"
TARGET_DIR="/home/aigc/temp/trans-test"
SOURCE_DIR="/home/aigc/data/aioutput/wavcl"
CONTROL_DIR="/tmp/ssh_control"

echo "============================================"
echo " SSH ControlMaster 链路复用测试"
echo "============================================"
echo "  远程:    ${USER}@${HOST}:${PORT}"
echo "  目标目录: ${TARGET_DIR}"
echo "  Control: ${CONTROL_DIR}"
echo "  ControlPersist: 600s (10 min)"
echo "============================================"
echo ""

# 收集 mp3 文件列表
mapfile -t FILES < <(find "$SOURCE_DIR" -maxdepth 1 -name "*.mp3" -type f | sort)
FILE_COUNT=${#FILES[@]}

if [ "$FILE_COUNT" -eq 0 ]; then
    echo "错误: 在 $SOURCE_DIR 下未找到 mp3 文件"
    exit 1
fi

echo "找到 ${FILE_COUNT} 个 mp3 文件"
echo ""

# 逐个传输
INDEX=1
for FILE in "${FILES[@]}"; do
    FILENAME=$(basename "$FILE")
    echo ""
    echo "--- [$INDEX/$FILE_COUNT] $FILENAME ---"
    echo ""

    python3 -m rsync_sync \
        -s "$FILE" \
        -t "$TARGET_DIR" \
        -p "${USER}@${HOST}:${PORT}" \
        --control-dir "$CONTROL_DIR" \
        --keep-control \
        --control-persist 600 \
        --slot-count 1 \
        -v

    INDEX=$((INDEX + 1))
done

echo ""
echo "============================================"
echo " 全部 ${FILE_COUNT} 个文件传输完成"
echo " ControlMaster socket 保持中，600s 空闲后自动清理"
echo ""
echo " 检查 socket: ls -la ${CONTROL_DIR}/"
echo " 手动清理:   ssh -S ${CONTROL_DIR}/control_* -O exit -p ${PORT} ${USER}@${HOST}"
echo "============================================"

# 显示 socket 文件
echo ""
if ls "$CONTROL_DIR"/control_* 2>/dev/null; then
    echo ""
    echo "Socket 文件仍存在，下次运行会复用。"
else
    echo "Socket 目录为空。"
fi