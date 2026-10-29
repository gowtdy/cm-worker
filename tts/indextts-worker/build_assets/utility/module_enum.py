from enum import Enum

# Define record types enum with integer values - 方式1：手动指定数字
class ToolType(Enum):
    """
    定义记录类型 - 整数值（手动指定）
    """
    COVER = 1     # 翻唱
    VOCAL_ISOLATOR = 2  # 人声提取
    VOCAL_REMOVER = 3  # 人声去除
    AUDIO_EXTRACTOR = 4  # 音频提取


class TaskStatus(Enum):
    """
    定义任务状态
    """
    QUEUED = "queued"  # 排队中
    PENDING = "pending"  # 待处理
    PROCESSING = "processing"  # 处理中
    CANCELLED = "cancelled"  # 已取消
    COMPLETE = "complete"  # 已完成
    FAILED = "failed"  # 失败


# Define module types enum
class ModuleType(Enum):
    """
    定义系统支持的模块类型
    """
    TTS = "tts"           # 文本转语音模块
    SOUND = "sound"       # 音频生成模块
    COVER = "cover"       # 翻唱
    VOCAL_ISOLATOR = "vocal_isolator" # 人声提取
    VOCAL_REMOVER = "vocal_remover" # 人声去除
    AUDIO_EXTRACTOR = "audio_extractor" # 音频提取
    
    @classmethod
    def get_all_values(cls):
        """获取所有模块类型的值"""
        return [member.value for member in cls]
    
    @classmethod
    def is_valid_module(cls, module: str) -> bool:
        """检查模块类型是否有效"""
        return module in cls.get_all_values()
    
    def __str__(self):
        return self.value