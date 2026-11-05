#!/bin/sh

if [ -d /mnt/ssh-keys ]; then
  RUNTIME_HOME="${HOME:-$(getent passwd "$(id -u)" | cut -d: -f6)}"
  if [ -n "$RUNTIME_HOME" ]; then
    mkdir -p "$RUNTIME_HOME"
    ln -snf /mnt/ssh-keys "$RUNTIME_HOME/.ssh"
  fi
fi

if [ -d "$HOME/.ssh" ]; then
    chmod 700 "$HOME/.ssh"
    if [ -f "$HOME/.ssh/id_rsa" ]; then
        chmod 600 "$HOME/.ssh/id_rsa"
    fi
    if [ -f "$HOME/.ssh/known_hosts" ]; then
        chmod 644 "$HOME/.ssh/known_hosts"
    fi
fi

export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export PATH="$HOME/.local/bin:$PATH"

PYTHONPATH=/app celery -A htmlvideo_task worker -Q us_htmlvideo_queue --pool=threads --concurrency=${CELERY_CONCURRENCY:-1} --loglevel=info
