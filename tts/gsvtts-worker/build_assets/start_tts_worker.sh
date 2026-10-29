#!/bin/sh

# Triton may run `df` on ~/.triton/autotune before it exists; pre-create to avoid stderr noise.
mkdir -p /home/aigc/.triton/autotune

# 设置SSH密钥权限
if [ -d "/home/aigc/.ssh" ]; then
    chmod 700 /home/aigc/.ssh
    # 检查并设置私钥权限（RSA或Ed25519）
    if [ -f "/home/aigc/.ssh/id_rsa" ]; then
        chmod 600 /home/aigc/.ssh/id_rsa
    fi
    # known_hosts文件用于避免首次连接确认
    if [ -f "/home/aigc/.ssh/known_hosts" ]; then
        chmod 644 /home/aigc/.ssh/known_hosts
    fi
fi

# 设置PyTorch内存分配器环境变量以减少内存碎片化
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export PATH="/home/aigc/.local/bin:$PATH"

PYTHONPATH=/app celery -A gpu_tts_tasks worker -Q gpu_tts_tasks --pool=solo --pool=threads --concurrency=${CELERY_CONCURRENCY:-1} --loglevel=info
