#!/bin/sh

if [ -d /mnt/ssh-keys ]; then
  RUNTIME_HOME="${HOME:-$(getent passwd "$(id -u)" | cut -d: -f6)}"
  if [ -n "$RUNTIME_HOME" ]; then
    mkdir -p "$RUNTIME_HOME"
    ln -snf /mnt/ssh-keys "$RUNTIME_HOME/.ssh"
  fi
fi

# Triton may run `df` on ~/.triton/autotune before it exists; pre-create to avoid stderr noise.
mkdir -p "$HOME/.triton/autotune"

# 设置SSH密钥权限
if [ -d "$HOME/.ssh" ]; then
    chmod 700 $HOME/.ssh
    # 检查并设置私钥权限（RSA或Ed25519）
    if [ -f "$HOME/.ssh/id_rsa" ]; then
        chmod 600 $HOME/.ssh/id_rsa
    fi
    # known_hosts文件用于避免首次连接确认
    if [ -f "$HOME/.ssh/known_hosts" ]; then
        chmod 644 $HOME/.ssh/known_hosts
    fi
fi

# 设置PyTorch内存分配器环境变量以减少内存碎片化
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export PATH="$HOME/.local/bin:$PATH"

PYTHONPATH=/app celery -A gpu_indextts_tasks worker -Q gpu_indextts25_tasks --pool=solo --pool=threads --concurrency=${CELERY_CONCURRENCY:-1} --loglevel=info
