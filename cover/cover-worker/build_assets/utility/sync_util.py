import subprocess
import os
import logging
import tempfile
import atexit
import time

# SSH ControlMaster 配置
# 使用持久化目录而不是临时目录，避免容器重启后丢失连接
# 优先使用 /app/data/.ssh_control（如果存在），否则使用用户主目录
SSH_CONTROL_DIR = '/app/data/ssh_control'
os.makedirs(SSH_CONTROL_DIR, mode=0o755, exist_ok=True)

# SSH 连接保持时间配置
# 可以设置为：
# - 数字（秒数）：连接在最后一次使用后保持指定秒数，例如 3600（1小时）、7200（2小时）
# - "yes"：连接永久保持，直到显式关闭或服务器断开（推荐用于生产环境）
# - "no"：连接立即关闭（不推荐）
SSH_CONTROL_PERSIST = "yes"  # 使用 "yes" 永久保持连接，或设置为秒数如 7200

# 注意：ControlMaxSessions 选项已移除
# 原因：某些 SSH 版本（特别是远程服务器）不支持该选项，会导致连接失败
# 替代方案：
# 1. 使用 SSH 默认值（通常为 10 个并发会话），这对大多数场景已足够
# 2. 如果需要更高并发，可以在 ~/.ssh/config 中为特定主机配置 ControlMaxSessions
# 3. 或者通过环境变量 SSH_CONFIG_FILE 指定自定义配置文件

# 存储已初始化的服务器连接
_initialized_servers = set()

def _get_control_path(server, proxy_port=None):
    """
    生成服务器对应的 ControlPath
    
    Args:
        server: 服务器地址，格式为 "user@host"
        proxy_port: 代理端口号，如果指定则在文件名中包含端口信息
    
    Returns:
        str: ControlPath 文件路径
    """
    # 将 user@host 转换为安全的文件名
    safe_name = server.replace('@', '_at_').replace(':', '_').replace('/', '_')
    # 如果指定了代理端口，添加到文件名中（用于区分不同的代理连接）
    if proxy_port:
        safe_name = f"{safe_name}_p{proxy_port}"
    return os.path.join(SSH_CONTROL_DIR, f'control_{safe_name}')

def initialize_ssh_connections(logger, servers=None, proxy_server=None, proxy_port=None):
    """
    初始化 SSH 连接，建立 ControlMaster 连接池
    
    Args:
        logger: 日志记录器
        servers (list, optional): 服务器列表，如果为None则从配置文件读取
        proxy_server (str, optional): 中转服务器地址（如果通过 socat 转发）
        proxy_port (int, optional): 中转服务器上的转发端口
    
    Returns:
        dict: 初始化结果，键为服务器地址，值为布尔值表示是否成功
    """
    if servers is None:
        servers = get_remote_servers()
    
    results = {}
    
    for server in servers:
        # 如果使用中转服务器，连接到中转服务器而不是最终目标
        if proxy_server and proxy_port:
            # 从 server 中提取用户名（如果格式为 user@host）
            if '@' in server:
                user = server.split('@')[0]
                # 连接到中转服务器
                connect_server = f"{user}@{proxy_server}"
            else:
                connect_server = proxy_server
        else:
            # 直接连接，不使用中转
            connect_server = server
        
        # 生成唯一的标识符（用于 _initialized_servers）
        server_key = f"{server}:{proxy_server}:{proxy_port}" if proxy_server else server
        
        if server_key in _initialized_servers:
            logger.debug(f"SSH connection already initialized for {server_key}")
            results[server] = True
            continue
            
        try:
            # ControlPath 使用最终目标服务器标识，但实际连接到中转服务器
            control_path = _get_control_path(server, proxy_port)
            
            # 使用 ssh 建立 ControlMaster 连接
            # ControlPersist 设置为永久保持（"yes"）或指定秒数
            # 注意：初始化时不使用 ControlMaxSessions，避免某些 SSH 版本不支持导致启动失败
            # 并发控制使用默认值（通常为 10），如果需要更高并发可以在 rsync 时设置
            ssh_cmd = [
                'ssh',
                '-o', 'ControlMaster=yes',
                '-o', f'ControlPath={control_path}',
                '-o', f'ControlPersist={SSH_CONTROL_PERSIST}',
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'BatchMode=yes',
            ]
            
            # 如果使用中转服务器，添加端口选项
            if proxy_server and proxy_port:
                ssh_cmd.extend(['-p', str(proxy_port)])
            
            # 添加目标服务器和测试命令
            ssh_cmd.extend([
                connect_server,
                'echo "SSH connection test"'
            ])
            
            process = subprocess.Popen(
                ssh_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            stdout, stderr = process.communicate(timeout=10)
            
            if process.returncode == 0:
                _initialized_servers.add(server_key)
                results[server] = True
                if proxy_server:
                    logger.info(f"SSH ControlMaster connection established for {server_key} via {connect_server}")
                else:
                    logger.info(f"SSH ControlMaster connection established for {server_key}")
            else:
                results[server] = False
                logger.warning(f"Failed to establish SSH ControlMaster for {server_key}: {stderr}")
                
        except subprocess.TimeoutExpired:
            process.kill()
            results[server] = False
            logger.error(f"Timeout establishing SSH connection for {server_key}")
        except Exception as e:
            results[server] = False
            logger.error(f"Exception establishing SSH connection for {server_key}: {str(e)}")
    
    return results

def cleanup_ssh_connections(logger):
    """
    清理所有 SSH ControlMaster 连接
    
    Args:
        logger: 日志记录器
    """
    for server_key in list(_initialized_servers):
        try:
            # 解析 server_key，格式可能是 "server" 或 "server:proxy_server:proxy_port"
            if ':' in server_key and server_key.count(':') >= 2:
                # 包含代理信息
                parts = server_key.split(':', 2)
                server = parts[0]
                proxy_server = parts[1]
                try:
                    proxy_port = int(parts[2])
                except (ValueError, IndexError):
                    proxy_port = None
            else:
                # 直接连接，不包含代理信息
                server = server_key
                proxy_server = None
                proxy_port = None
            
            # 获取 ControlPath（使用最终目标服务器标识和代理端口）
            control_path = _get_control_path(server, proxy_port)
            
            if os.path.exists(control_path):
                # 如果使用代理，需要连接到代理服务器
                if proxy_server and proxy_port:
                    # 从 server 中提取用户名（如果格式为 user@host）
                    if '@' in server:
                        user = server.split('@')[0]
                        connect_server = f"{user}@{proxy_server}"
                    else:
                        connect_server = proxy_server
                else:
                    connect_server = server
                
                # 使用 ssh -O exit 来关闭 ControlMaster 连接
                ssh_cmd = [
                    'ssh',
                    '-O', 'exit',
                    '-o', f'ControlPath={control_path}',
                ]
                
                # 如果使用代理，添加端口选项
                if proxy_server and proxy_port:
                    ssh_cmd.extend(['-p', str(proxy_port)])
                
                ssh_cmd.append(connect_server)
                
                subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                logger.debug(f"Closed SSH ControlMaster connection for {server_key}")
        except Exception as e:
            logger.warning(f"Error closing SSH connection for {server_key}: {str(e)}")
    
    _initialized_servers.clear()

# 注册退出时的清理函数
atexit.register(lambda: cleanup_ssh_connections(logging.getLogger('root')))

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

def get_proxy_config():
    """
    从配置文件获取代理服务器配置，如果文件不存在则返回 None
    
    Returns:
        tuple: (proxy_server, proxy_port) 如果配置存在，否则返回 (None, None)
    """
    config_file = "data/remote_rsync_proxy"
    
    try:
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                content = f.read().strip()
                if content:  # 确保读取的内容非空
                    # 按行分割，过滤空行和注释行
                    for line in content.split('\n'):
                        line = line.strip()
                        if line and not line.startswith('#'):
                            # 解析格式：proxy_server:proxy_port
                            if ':' in line:
                                parts = line.split(':', 1)
                                proxy_server = parts[0].strip()
                                try:
                                    proxy_port = int(parts[1].strip())
                                    return (proxy_server, proxy_port)
                                except ValueError:
                                    logging.warning(f"Invalid proxy port in config: {parts[1]}")
                                    return (None, None)
                            else:
                                logging.warning(f"Invalid proxy config format: {line}")
                                return (None, None)
        
        # 如果文件不存在或为空，返回 None
        return (None, None)
    except Exception as e:
        logging.warning(f"Error reading proxy config: {str(e)}")
        return (None, None)

def sync_files(logger, source_file, target_server, target_dir, proxy_server=None, proxy_port=None):
    """
    在处理完成后同步音频文件到目标服务器，使用 SSH ControlMaster 复用连接
    
    Args:
        logger: 日志记录器
        source_file: 源文件路径
        target_server: 目标服务器地址，格式为 "user@host"
        target_dir: 目标目录路径
        proxy_server: 中转服务器地址（如果通过 socat 转发）
        proxy_port: 中转服务器上的转发端口
    
    Returns:
        bool: 同步成功返回 True，否则返回 False
    """
    try:
        # ControlPath 使用最终目标服务器标识（包含代理端口信息）
        control_path = _get_control_path(target_server, proxy_port)
        
        # 如果使用中转服务器，连接到中转服务器
        if proxy_server and proxy_port:
            if '@' in target_server:
                user = target_server.split('@')[0]
                # 连接到中转服务器
                connect_server = f"{user}@{proxy_server}"
            else:
                connect_server = proxy_server
        else:
            # 直接连接
            connect_server = target_server
        
        # 构建 SSH 选项，使用 ControlMaster 复用连接
        # ControlMaster=auto: 自动使用已存在的 ControlMaster，如果不存在则创建
        # ControlPersist: 连接保持时间（从全局配置读取）
        # 注意：不使用 ControlMaxSessions，使用 SSH 默认值（通常为 10）
        # 这样可以兼容所有 SSH 版本，默认值通常已足够大多数场景
        ssh_opts = [
            '-o', 'ControlMaster=auto',
            '-o', f'ControlPath={control_path}',
            '-o', f'ControlPersist={SSH_CONTROL_PERSIST}',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'BatchMode=yes'
        ]
        
        # 如果使用中转服务器，添加端口选项
        if proxy_server and proxy_port:
            ssh_opts.extend(['-p', str(proxy_port)])
        
        # 将 SSH 选项转换为字符串
        ssh_opts_str = ' '.join(ssh_opts)
        
        # 构建完整的 rsync 命令
        # 使用 -e 选项传递 SSH 参数给 rsync
        # 注意：target_dir 是最终目标服务器上的路径
        cmd = (
            f'rsync -av --partial '
            f'-e "ssh {ssh_opts_str}" '
            f'--rsync-path="mkdir -p {target_dir} && rsync" '
            f'{source_file} {connect_server}:{target_dir}/'
        )
        
        # 使用 shell=True 执行命令（因为需要处理引号和 SSH 选项）
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
            logger.error(f"Rsync failed: {stderr}")
            logger.error(f"Command was: {cmd}")
            return False
        
        logger.info(f"Successfully synced file {source_file} to {target_server}:{target_dir}/, cmd: {cmd}")
        return True
    except Exception as e:
        logger.error(f"Exception during rsync: {str(e)}")
        return False

def scp_files(logger, source_file, target_server, target_dir, proxy_server=None, proxy_port=None):
    """
    在处理完成后同步音频文件到目标服务器
    
    Args:
        logger: 日志记录器
        source_file: 源文件路径
        target_server: 目标服务器地址，格式为 "user@host"
        target_dir: 目标目录路径
        proxy_server: 中转服务器地址（如果通过 socat 转发）
        proxy_port: 中转服务器上的转发端口
    
    Returns:
        bool: 同步成功返回 True，否则返回 False
    """
    try:
        # 如果使用中转服务器，连接到中转服务器
        if proxy_server and proxy_port:
            if '@' in target_server:
                user = target_server.split('@')[0]
                # 连接到中转服务器
                connect_server = f"{user}@{proxy_server}"
            else:
                connect_server = proxy_server
            
            # 构建带端口的 SSH/SCP 命令
            port_option = f"-p {proxy_port}"
            mkdir_cmd = f'ssh {port_option} {connect_server} "mkdir -p {target_dir}"'
            scp_cmd = f'scp {port_option} {source_file} {connect_server}:{target_dir}/'
        else:
            # 直接连接
            connect_server = target_server
            mkdir_cmd = f'ssh {connect_server} "mkdir -p {target_dir}"'
            scp_cmd = f'scp {source_file} {connect_server}:{target_dir}/'
        
        # 先执行 mkdir 命令
        mkdir_process = subprocess.Popen(
            mkdir_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        mkdir_stdout, mkdir_stderr = mkdir_process.communicate()
        
        if mkdir_process.returncode != 0:
            logger.error(f"Failed to create directory on remote server: {mkdir_stderr}")
            logger.error(f"Command was: {mkdir_cmd}")
            return False
        
        # 执行 scp 命令
        scp_process = subprocess.Popen(
            scp_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        scp_stdout, scp_stderr = scp_process.communicate()
        
        if scp_process.returncode != 0:
            logger.error(f"SCP failed: {scp_stderr}")
            logger.error(f"Command was: {scp_cmd}")
            return False
        
        logger.info(f"Successfully synced file {source_file} to {target_server}:{target_dir}/")
        return True
    except Exception as e:
        logger.error(f"Exception during scp: {str(e)}")
        return False

def sync_files_to_multiple_servers(logger, source_file, target_dir, servers=None, proxy_server=None, proxy_port=None):
    """
    同步文件到多个远程服务器，使用 SSH ControlMaster 复用连接
    
    Args:
        logger: 日志记录器
        source_file (str): 要同步的源文件路径
        target_dir (str): 目标目录路径
        servers (list, optional): 服务器列表，如果为None则从配置文件读取
        proxy_server (str, optional): 中转服务器地址（如果通过 socat 转发）
        proxy_port (int, optional): 中转服务器上的转发端口
    
    Returns:
        bool: 所有服务器同步成功返回 True，否则返回 False
    """
    if servers is None:
        servers = get_remote_servers()
    
    # 如果未提供代理参数，尝试从配置文件读取
    if proxy_server is None or proxy_port is None:
        config_proxy_server, config_proxy_port = get_proxy_config()
        if config_proxy_server and config_proxy_port:
            proxy_server = config_proxy_server
            proxy_port = config_proxy_port
            logger.debug(f"Using proxy server from config: {proxy_server}:{proxy_port}")
    
    # 确保所有服务器的 SSH 连接已初始化（如果还没有的话）
    # 这里使用懒加载方式，第一次调用时会自动建立连接
    for server in servers:
        # 生成服务器键（包含代理信息）
        server_key = f"{server}:{proxy_server}:{proxy_port}" if proxy_server else server
        
        if server_key not in _initialized_servers:
            # 尝试初始化连接（非阻塞，如果失败会回退到普通连接）
            try:
                control_path = _get_control_path(server, proxy_port)
                # 检查控制套接字是否存在
                if not os.path.exists(control_path):
                    # 异步初始化连接（不阻塞当前操作）
                    initialize_ssh_connections(logger, [server], proxy_server, proxy_port)
            except Exception as e:
                logger.debug(f"Could not pre-initialize SSH connection for {server_key}: {e}")
    
    ret = True
    results = {}
    
    # 获取源文件大小
    file_size = 0
    if os.path.exists(source_file):
        file_size = os.path.getsize(source_file)
    
    # 记录总开始时间
    total_start_time = time.time()
    
    for server in servers:
        logger.info(f"Syncing to server: {server}, proxy_server: {proxy_server}, proxy_port: {proxy_port}")
        success = sync_files(logger, source_file, server, target_dir, proxy_server, proxy_port)
        results[server] = success
        
        if success:
            logger.info(f"Successfully synced to {server}")
        else:
            logger.error(f"Failed to sync to {server}")
    
    # 计算总时间
    total_end_time = time.time()
    total_elapsed_time = total_end_time - total_start_time
    
    # 统计成功和失败的数量
    success_count = sum(1 for success in results.values() if success)
    total_count = len(results)
    
    # 格式化文件大小
    if file_size < 1024:
        size_str = f"{file_size} B"
    elif file_size < 1024 * 1024:
        size_str = f"{file_size / 1024:.2f} KB"
    else:
        size_str = f"{file_size / (1024 * 1024):.2f} MB"
    
    # 计算平均传输速度（MB/s）
    avg_speed_mbps = 0
    if total_elapsed_time > 0 and file_size > 0:
        avg_speed_mbps = (file_size / (1024 * 1024)) / total_elapsed_time
    
    logger.info(
        f"Sync summary: {success_count}/{total_count} servers successful | "
        f"Size: {size_str} | "
        f"Total time: {total_elapsed_time:.2f}s | "
        f"Avg speed: {avg_speed_mbps:.2f} MB/s"
    )
    
    if success_count != total_count:
        ret = False

    return ret
