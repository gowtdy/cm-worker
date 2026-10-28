import logging
import os


logger = logging.getLogger()  # 不加名称 设置root logger

def logset(logger, logfile):
    LOG_FORMAT = "%(asctime)s-%(filename)s[line:%(lineno)d]-%(levelname)s: %(message)s"

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(LOG_FORMAT, datefmt='%Y-%m-%d %H:%M:%S')
    
    # 使用StreamHandler输出到屏幕
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    
    # 尝试使用FileHandler输出到文件，如果失败则只使用控制台输出
    try:
        # 确保日志目录存在
        log_dir = os.path.dirname(logfile)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
            
        fh = logging.FileHandler(logfile)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        logger.info(f"logfile set: {logfile}")
    except (PermissionError, OSError) as e:
        logger.warning(f"failed to create logfile {logfile}: {e}, will only use console output")


