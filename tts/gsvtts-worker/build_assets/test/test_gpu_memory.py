#!/usr/bin/env python3
"""
GPU内存管理测试脚本
用于验证CUDA错误修复效果
"""

import os
import sys
import torch
import gc
import logging

# 设置日志
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def test_gpu_memory_management():
    """测试GPU内存管理功能"""
    if not torch.cuda.is_available():
        logger.warning("CUDA not available, skipping GPU tests")
        return
    
    device = torch.device("cuda:0")
    
    # 测试内存分配和清理
    logger.info("Testing GPU memory management...")
    
    # 分配一些内存
    tensor1 = torch.randn(1000, 1000).to(device)
    tensor2 = torch.randn(1000, 1000).to(device)
    
    # 检查内存使用情况
    allocated = torch.cuda.memory_allocated(device)
    reserved = torch.cuda.memory_reserved(device)
    logger.info(f"After allocation - Allocated: {allocated/1024**2:.2f}MB, Reserved: {reserved/1024**2:.2f}MB")
    
    # 清理内存
    del tensor1, tensor2
    torch.cuda.empty_cache()
    gc.collect()
    
    # 再次检查内存
    allocated = torch.cuda.memory_allocated(device)
    reserved = torch.cuda.memory_reserved(device)
    logger.info(f"After cleanup - Allocated: {allocated/1024**2:.2f}MB, Reserved: {reserved/1024**2:.2f}MB")
    
    logger.info("GPU memory management test completed")

def test_numpy_to_tensor_conversion():
    """测试numpy到tensor的转换"""
    import numpy as np
    
    logger.info("Testing numpy to tensor conversion...")
    
    # 创建测试数据
    test_data = np.random.randn(100, 100).astype(np.float32)
    
    try:
        # 测试正常转换
        tensor = torch.from_numpy(test_data)
        logger.info(f"Successfully converted numpy array of shape {test_data.shape}")
        
        # 测试移动到GPU
        if torch.cuda.is_available():
            device = torch.device("cuda:0")
            tensor_gpu = tensor.to(device)
            logger.info(f"Successfully moved tensor to GPU")
            
            # 检查数据有效性
            if torch.isnan(tensor_gpu).any():
                logger.error("Tensor contains NaN values")
            elif torch.isinf(tensor_gpu).any():
                logger.error("Tensor contains Inf values")
            else:
                logger.info("Tensor data validation passed")
                
    except Exception as e:
        logger.error(f"Error in numpy to tensor conversion: {e}")
        raise

def test_error_handling():
    """测试错误处理机制"""
    logger.info("Testing error handling...")
    
    try:
        # 尝试加载不存在的文件
        non_existent_path = "/path/to/non/existent/file.npy"
        if os.path.exists(non_existent_path):
            logger.warning("Test file unexpectedly exists")
        else:
            logger.info("Non-existent file test passed")
            
    except Exception as e:
        logger.error(f"Unexpected error in error handling test: {e}")

if __name__ == "__main__":
    logger.info("Starting GPU memory management tests...")
    
    try:
        test_gpu_memory_management()
        test_numpy_to_tensor_conversion()
        test_error_handling()
        logger.info("All tests completed successfully")
    except Exception as e:
        logger.error(f"Test failed: {e}")
        sys.exit(1) 