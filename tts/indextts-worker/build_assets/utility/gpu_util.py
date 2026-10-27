import torch
import gc
import time

# CUDA错误处理装饰器
def cuda_error_handler(logger):
    """装饰器工厂：为任务函数捕获并处理 CUDA 相关错误"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except RuntimeError as e:
                if "CUDA" in str(e) or "device-side assert" in str(e):
                    logger.error(f'CUDA error in {func.__name__}: {e}')
                    # 尝试恢复 CUDA 状态
                    try:
                        if torch.cuda.is_available():
                            torch.cuda.synchronize()
                            torch.cuda.empty_cache()
                            gc.collect()
                    except Exception as cleanup_error:
                        logger.error(f'Failed to cleanup after CUDA error: {cleanup_error}')
                    raise
                raise
            except Exception as e:
                logger.error(f'Unexpected error in {func.__name__}: {e}')
                raise
        return wrapper
    return decorator

def check_and_wait_for_gpu_memory(logger, device, min_free_gb=2.0, max_wait_seconds=30):
    """检查GPU内存，如果不足则等待并清理
    
    Args:
        min_free_gb: 最小需要的空闲内存（GB）
        max_wait_seconds: 最大等待时间（秒）
    
    Returns:
        bool: 如果内存足够返回True，否则返回False
    """
    if not torch.cuda.is_available():
        return True
    
    start_time = time.time()
    while time.time() - start_time < max_wait_seconds:
        try:
            gpu_memory = torch.cuda.get_device_properties(device).total_memory
            allocated_memory = torch.cuda.memory_allocated(device)
            cached_memory = torch.cuda.memory_reserved(device)
            free_memory = gpu_memory - cached_memory
            
            if free_memory >= min_free_gb * 1024**3:
                logger.debug(f'GPU memory sufficient. Free: {free_memory/1024**3:.2f}GB')
                return True
            else:
                logger.warning(f'GPU memory insufficient, waiting... Free: {free_memory/1024**3:.2f}GB, Required: {min_free_gb:.2f}GB')
                # 尝试清理内存
                torch.cuda.empty_cache()
                gc.collect()
                time.sleep(2)
        except Exception as e:
            logger.warning(f'Error checking GPU memory: {e}')
            time.sleep(1)
    
    # 超时后再次检查
    try:
        gpu_memory = torch.cuda.get_device_properties(device).total_memory
        cached_memory = torch.cuda.memory_reserved(device)
        free_memory = gpu_memory - cached_memory
        return free_memory >= min_free_gb * 1024**3
    except:
        return False

def manage_gpu_memory(logger, device):
    """管理GPU内存，清理缓存并检查内存状态"""
    if not torch.cuda.is_available():
        return True
        
    try:
        # 检查CUDA设备状态
        # device 可能是字符串 "cuda:0" 或 torch.device 对象
        if isinstance(device, str) and device.startswith('cuda:'):
            try:
                device_index = int(device.split(':')[1])
                if device_index >= torch.cuda.device_count():
                    logger.warning(f'Device {device} is invalid, skipping GPU memory management')
                    return False
                if torch.cuda.current_device() != device_index:
                    torch.cuda.set_device(device_index)
            except (ValueError, IndexError):
                logger.warning(f'Invalid device format {device}, skipping GPU memory management')
                return False
        elif hasattr(device, 'index'):
            if torch.cuda.current_device() != device.index:
                torch.cuda.set_device(device.index)
        
        # 同步CUDA操作，确保所有操作完成
        torch.cuda.synchronize()
        
        # 安全地清理GPU缓存
        try:
            torch.cuda.empty_cache()
        except RuntimeError as e:
            logger.warning(f'Failed to empty CUDA cache: {e}')
            # 如果清理失败，尝试重置设备
            try:
                if isinstance(device, str) and device.startswith('cuda:'):
                    device_obj = torch.device(device)
                else:
                    device_obj = device
                torch.cuda.reset_peak_memory_stats(device_obj)
            except:
                pass
        
        # 强制垃圾回收
        gc.collect()
        
        # 获取内存信息
        try:
            # 获取设备索引
            if isinstance(device, str) and device.startswith('cuda:'):
                device_index = int(device.split(':')[1])
                device_obj = torch.device(device)
            else:
                device_obj = device
                device_index = device_obj.index if hasattr(device_obj, 'index') else 0
            
            gpu_memory = torch.cuda.get_device_properties(device_obj).total_memory
            allocated_memory = torch.cuda.memory_allocated(device_obj)
            cached_memory = torch.cuda.memory_reserved(device_obj)
            free_memory = gpu_memory - allocated_memory
            
            logger.debug(f'GPU Memory Status - Total: {gpu_memory/1024**3:.2f}GB, '
                        f'Allocated: {allocated_memory/1024**3:.2f}GB, '
                        f'Cached: {cached_memory/1024**3:.2f}GB, '
                        f'Free: {free_memory/1024**3:.2f}GB')
            
            # 如果可用内存不足，强制清理
            if free_memory < 1 * 1024**3:  # 小于1GB时清理
                logger.warning(f'GPU memory critically low, forcing cleanup. Free: {free_memory/1024**3:.2f}GB')
                try:
                    torch.cuda.empty_cache()
                    gc.collect()
                except RuntimeError as e:
                    logger.error(f'Failed to cleanup GPU memory: {e}')
                    return False
                return False
            return True
        except RuntimeError as e:
            logger.error(f'Failed to get GPU memory info: {e}')
            return False
            
    except Exception as e:
        logger.error(f'Error in manage_gpu_memory: {e}')
        return False        


def reset_cuda_device(logger, device):
    """尝试重置 CUDA 设备状态"""
    try:
        if isinstance(device, str) and device.startswith('cuda:'):
            device_obj = torch.device(device)
            device_index = int(device.split(':')[1])
        else:
            device_obj = device
            device_index = device_obj.index if hasattr(device_obj, 'index') else 0
        
        # 1. 同步所有 CUDA 操作
        torch.cuda.synchronize(device_obj)
        
        # 2. 清理缓存
        torch.cuda.empty_cache()
        
        # 3. 重置内存统计
        torch.cuda.reset_peak_memory_stats(device_obj)
        
        # 4. 强制垃圾回收
        gc.collect()
        
        # 5. 再次清理缓存
        torch.cuda.empty_cache()
        
        logger.info(f'CUDA device {device_index} reset attempted')
        return True
    except Exception as e:
        logger.error(f'Failed to reset CUDA device: {e}')
        return False

def log_gpu_error_details(logger, device, error):
    """记录 GPU 错误的详细信息"""
    try:
        if isinstance(device, str) and device.startswith('cuda:'):
            device_index = int(device.split(':')[1])
        else:
            device_index = device.index if hasattr(device, 'index') else 0
        
        # 获取 GPU 信息
        if torch.cuda.is_available() and device_index < torch.cuda.device_count():
            props = torch.cuda.get_device_properties(device_index)
            logger.error(f'GPU Error Details:')
            logger.error(f'  Device: {props.name} (Index: {device_index})')
            logger.error(f'  Total Memory: {props.total_memory / 1024**3:.2f}GB')
            
            try:
                allocated = torch.cuda.memory_allocated(device_index)
                reserved = torch.cuda.memory_reserved(device_index)
                logger.error(f'  Allocated: {allocated / 1024**3:.2f}GB')
                logger.error(f'  Reserved: {reserved / 1024**3:.2f}GB')
            except:
                pass
        
        logger.error(f'  Error: {error}')
        logger.error(f'  Suggestion: This may require worker restart or GPU driver reset')
        
    except Exception as e:
        logger.error(f'Failed to log GPU error details: {e}')

def check_gpu_health(logger, device, attempt_recovery=True):
    """检查 GPU 健康状态，可尝试恢复
    
    Args:
        device: GPU 设备
        attempt_recovery: 如果检测到错误，是否尝试恢复
    
    Returns:
        bool: GPU 是否健康
    """
    try:
        if not torch.cuda.is_available():
            return True  # CPU 模式总是健康的
        
        # 获取设备对象
        if isinstance(device, str) and device.startswith('cuda:'):
            device_obj = torch.device(device)
            device_index = int(device.split(':')[1])
        else:
            device_obj = device
            device_index = device_obj.index if hasattr(device_obj, 'index') else 0
        
        # 检查设备是否有效
        if device_index >= torch.cuda.device_count():
            logger.error(f'Invalid device index: {device_index}')
            return False
        
        # 设置当前设备
        torch.cuda.set_device(device_index)
        
        # 创建一个小的测试 tensor 来验证 GPU 是否正常工作
        test_tensor = torch.randn(10, 10, device=device_obj)
        result = test_tensor @ test_tensor.T
        torch.cuda.synchronize(device_obj)
        
        # 清理测试 tensor
        del test_tensor, result
        torch.cuda.empty_cache()
        
        return True
        
    except RuntimeError as e:
        error_str = str(e)
        logger.error(f'GPU health check failed: {error_str}')
        
        # 检查是否是 device-side assert 错误
        if "device-side assert" in error_str.lower():
            logger.critical(f'CUDA device-side assert detected on device {device_index}!')
            
            if attempt_recovery:
                logger.warning('Attempting to recover GPU state...')
                # 尝试重置设备
                if reset_cuda_device(logger, device):
                    # 等待一段时间让设备恢复
                    time.sleep(2)
                    # 再次尝试健康检查（不尝试恢复，避免无限循环）
                    try:
                        test_tensor = torch.randn(5, 5, device=device_obj)
                        torch.cuda.synchronize(device_obj)
                        del test_tensor
                        torch.cuda.empty_cache()
                        logger.info('GPU recovery successful')
                        return True
                    except Exception as retry_e:
                        logger.error(f'GPU recovery failed: {retry_e}')
                        logger.critical('GPU may need process restart or driver reset')
                        return False
                else:
                    logger.critical('Failed to reset CUDA device, GPU may be in unrecoverable state')
                    return False
            else:
                logger.critical('GPU in error state, recovery not attempted')
                return False
        else:
            # 其他 CUDA 错误
            if attempt_recovery:
                logger.warning('Attempting to recover from CUDA error...')
                reset_cuda_device(logger, device)
                time.sleep(1)
                # 简单重试
                try:
                    test_tensor = torch.randn(5, 5, device=device_obj)
                    torch.cuda.synchronize(device_obj)
                    del test_tensor
                    torch.cuda.empty_cache()
                    return True
                except:
                    return False
            return False
            
    except Exception as e:
        logger.error(f'Unexpected error in GPU health check: {e}')
        return False
