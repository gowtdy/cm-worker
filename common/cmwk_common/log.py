import logging
from logging.handlers import TimedRotatingFileHandler
import os
from contextvars import ContextVar
from typing import Optional

logger = logging.getLogger()  # 不加名称 设置root logger

_reqid_var: ContextVar[str] = ContextVar("reqid", default="-")

DEFAULT_LOG_FORMAT = "%(asctime)s-%(filename)s[line:%(lineno)d]-%(levelname)s-[reqid:%(reqid)s]: %(message)s"


def set_reqid(reqid: Optional[str]) -> None:
    if reqid is None or str(reqid).strip() == "":
        _reqid_var.set("-")
        return
    _reqid_var.set(str(reqid))


def clear_reqid() -> None:
    _reqid_var.set("-")


def get_reqid() -> str:
    return _reqid_var.get()


class ReqIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.reqid = get_reqid()
        return True


def _add_reqid_filter(handler: logging.Handler) -> None:
    handler.addFilter(ReqIdFilter())


def setup_logger(
    logger_name: str,
    log_file: str = "logs/voice_log.txt",
    console_level: str = "INFO",
    file_level: str = "INFO",
    when: str = 'midnight',  # 每天午夜切分
    backup_count: int = 30,  # 保留30天的日志
    log_format: str = DEFAULT_LOG_FORMAT
) -> logging.Logger:
    """
    配置并返回logger实例
    
    Args:
        log_file: 日志文件路径
        console_level: 控制台日志级别
        file_level: 文件日志级别
        when: 日志切分时间
            'S': 秒
            'M': 分钟
            'H': 小时
            'D': 天
            'midnight': 每天午夜
        backup_count: 保留的日志文件数量
        logger_name: logger名称，若为None则使用root logger
        log_format: 日志格式
    """
    # 创建日志目录
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    logging.getLogger('pydub.converter').setLevel(logging.WARNING)
    # 获取logger实例
    logger = logging.getLogger(logger_name)
    
    # 如果logger已经配置过handler，直接返回
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(log_format, datefmt='%Y-%m-%d %H:%M:%S')

    # 设置文件处理器（按时间切分）
    fh = TimedRotatingFileHandler(
        log_file,
        when=when,
        backupCount=backup_count,
        encoding='utf-8'
    )
    # 设置日志文件后缀名
    fh.suffix = "%Y-%m-%d.log"
    fh.setLevel(getattr(logging, file_level.upper()))
    fh.setFormatter(formatter)
    _add_reqid_filter(fh)

    # 设置控制台处理器
    ch = logging.StreamHandler()
    ch.setLevel(getattr(logging, console_level.upper()))
    ch.setFormatter(formatter)
    _add_reqid_filter(ch)

    logger.addHandler(ch)
    logger.addHandler(fh)
    
    return logger

def logset(logger, logfile):
    LOG_FORMAT = DEFAULT_LOG_FORMAT

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(LOG_FORMAT, datefmt='%Y-%m-%d %H:%M:%S')
    
    # 使用FileHander输出到文件
    fh = logging.FileHandler(logfile)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    _add_reqid_filter(fh)
    
    # 使用StreamHandler输出到屏幕
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(formatter)
    _add_reqid_filter(ch)
    
    # 添加两个Handler
    logger.addHandler(ch)
    logger.addHandler(fh)
