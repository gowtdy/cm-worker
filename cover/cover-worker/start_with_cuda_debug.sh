#!/bin/bash

# 设置CUDA调试环境变量
export CUDA_LAUNCH_BLOCKING=1
export TORCH_USE_CUDA_DSA=1
export CUDA_DEVICE_ORDER=PCI_BUS_ID

# 设置PyTorch相关环境变量
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
export TORCH_CUDNN_V8_API_ENABLED=1

# 打印环境信息
echo "CUDA Debug Environment Variables Set:"
echo "CUDA_LAUNCH_BLOCKING=$CUDA_LAUNCH_BLOCKING"
echo "TORCH_USE_CUDA_DSA=$TORCH_USE_CUDA_DSA"
echo "CUDA_DEVICE_ORDER=$CUDA_DEVICE_ORDER"
echo "PYTORCH_CUDA_ALLOC_CONF=$PYTORCH_CUDA_ALLOC_CONF"

# 检查CUDA可用性
python3 -c "import torch; print(f'CUDA Available: {torch.cuda.is_available()}'); print(f'CUDA Device Count: {torch.cuda.device_count()}'); print(f'Current Device: {torch.cuda.current_device() if torch.cuda.is_available() else \"N/A\"}')"

# 启动你的应用程序
# 例如: python your_app.py
echo "Environment ready. You can now start your application." 