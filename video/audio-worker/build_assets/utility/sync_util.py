import subprocess
import os
import logging

def get_remote_server():
    """
    从配置文件获取远程服务器信息，如果文件不存在则返回默认值
    
    Returns:
        str: 远程服务器连接字符串，格式为"user@host"
    """
    default_server = "aigc@154.205.139.58"
    config_file = "data/remote_rsync_server"
    
    try:
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                server = f.read().strip()
                if server:  # 确保读取的内容非空
                    return server
                
        # 如果文件不存在或为空，返回默认值
        return default_server
    except Exception as e:
        logging.warning(f"Error reading remote server config: {str(e)}, using default server")
        return default_server

def get_remote_servers():
    """
    从配置文件获取多个远程服务器信息，如果文件不存在则返回默认值列表
    
    Returns:
        list: 远程服务器连接字符串列表，每个元素格式为"user@host"
    """
    default_servers = ["aigc@154.205.139.58"]
    config_file = "data/remote_rsync_servers"
    
    try:
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                content = f.read().strip()
                if content:  # 确保读取的内容非空
                    # 按行分割，过滤空行和注释行
                    servers = []
                    for line in content.split('\n'):
                        line = line.strip()
                        if line and not line.startswith('#'):
                            servers.append(line)
                    
                    if servers:  # 确保解析到有效的服务器
                        return servers
                
        # 如果文件不存在或为空，返回默认值
        return default_servers
    except Exception as e:
        logging.warning(f"Error reading remote servers config: {str(e)}, using default servers")
        return default_servers

def sync_files(logger, source_file, target_server, target_dir):
    """在处理完成后同步音频文件到目标服务器"""
    try:
        cmd = f'rsync -avz --rsync-path="mkdir -p {target_dir} && rsync" {source_file} {target_server}:{target_dir}/'
        
        # 使用正确的参数创建进程
        process = subprocess.Popen(
            cmd, 
            shell=True, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            logger.error(f"Rsync failed: {stderr}")  # 已经是字符串，不需要decode
            logger.error(f"Command was: {cmd}")
            return False
        
        logger.info(f"Successfully synced file {source_file} to {target_server}:{target_dir}/")
        return True
    except Exception as e:
        logger.error(f"Exception during rsync: {str(e)}")
        return False

def sync_files_to_multiple_servers(logger, source_file, target_dir, servers=None):
    """
    同步文件到多个远程服务器
    
    Args:
        logger: 日志记录器
        source_file (str): 要同步的源文件路径
        target_dir (str): 目标目录路径
        servers (list, optional): 服务器列表，如果为None则从配置文件读取
    
    Returns:
        dict: 同步结果字典，键为服务器地址，值为布尔值表示是否成功
    """
    if servers is None:
        servers = get_remote_servers()
    
    ret = True
    results = {}
    
    for server in servers:
        logger.info(f"Syncing to server: {server}")
        success = sync_files(logger, source_file, server, target_dir)
        results[server] = success
        
        if success:
            logger.info(f"Successfully synced to {server}")
        else:
            logger.error(f"Failed to sync to {server}")
    
    # 统计成功和失败的数量
    success_count = sum(1 for success in results.values() if success)
    total_count = len(results)
    
    logger.info(f"Sync summary: {success_count}/{total_count} servers successful")
    
    if success_count != total_count:
        ret = False

    return ret