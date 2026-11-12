from enum import Enum


class AuthEnum(Enum):
    ECHOBAT_AUTH = "echobeat_auth"
    AIVOICELAB_AUTH = "aivoice_auth"
    TIKTOKVOICE_AUTH = "tiktokvoice_auth"
    CALUMA_AUTH = "caluma_auth"


class UserEnum(Enum):
    ECHOBAT_USER = "echobeat_user"
    AIVOICELAB_USER = "aivoice_user"
    TIKTOKVOICE_USER = "tiktokvoice_user"
    CALUMA_USER = "caluma_user"


class ToolType(Enum):
    """定义记录类型 - 整数值（手动指定）"""
    COVER = 1
    VOCAL_ISOLATOR = 2
    VOCAL_REMOVER = 3
    AUDIO_EXTRACTOR = 4


class TaskStatus(Enum):
    """定义任务状态"""
    QUEUED = "queued"
    PENDING = "pending"
    PROCESSING = "processing"
    CANCELLED = "cancelled"
    COMPLETE = "complete"
    FAILED = "failed"
    # video 生成过程状态
    VIDEO_INITED = "video_inited"
    VIDEO_STORIED = "video_storied"
    VIDEO_TTSED = "video_ttsed"
    VIDEO_VISUALED = "video_visualed"
    VIDEO_FRAMED = "video_framed"


class ModuleType(Enum):
    """定义系统支持的模块类型"""
    TTS = "tts"
    SOUND = "sound"
    COVER = "cover"
    VOCAL_ISOLATOR = "vocal_isolator"
    VOCAL_REMOVER = "vocal_remover"
    AUDIO_EXTRACTOR = "audio_extractor"
    TRANSCRIBE = "transcribe"
    VIDEO_INTENT = "video_intent"
    VIDEO_CREATE = "video_create"

    @classmethod
    def get_all_values(cls):
        return [member.value for member in cls]

    @classmethod
    def is_valid_module(cls, module: str) -> bool:
        return module in cls.get_all_values()

    def __str__(self):
        return self.value


class ActionType(Enum):
    """定义用户行为类型"""
    UNKNOWN = "unknown"
    PAYMODAL_UPGRADE = "paymodal-upgrade"
    PRICING_POP_LOGIN = "pricing-pop-login"
    PRICING_CHECKOUT_SUCCESS = "pricing-checkout-success"
    PRICING_CHECKOUT_FAILED = "pricing-checkout-failed"

    GOOGLE_SEARCH = "google-search"
    UPLOAD_AUDIO = "cover-upload-audio"
    RECORD_AUDIO = "cover-record-audio"

    COVER_SONG_GENERATE = "cover-song-generate"
    COVER_SONG_SAMPLE_GENERATE = "cover-song-sample-generate"
    COVER_SAMPLE_GENERATE = "cover-sample-generate"
    COVER_SONG_PLAY = "cover-song-play"
    COVER_SONG_GENPOP_LOGIN = "cover-song-genpop-login"
    COVER_SONG_GENPOP_SUBSCRIPT = "cover-song-genpop-subscript"
    COVER_SONG_DOWNLOAD_LOGIN = "cover-song-download-login"
    COVER_SONG_DOWNLOAD = "cover-song-download"
    COVER_SONG_DOWNPOP_SUBSCRIPT = "cover-song-downpop-subscript"
    COVER_SONG_EXHAUSTED_SUBSCRIPT = "cover-song-exhausted-subscript"

    TTS_GENERATE = "tts-generate"
    TTS_PLAY = "tts-play"
    TTS_GENPOP_LOGIN = "tts-genpop-login"
    TTS_GENPOP_SUBSCRIPT = "tts-genpop-subscript"
    TTS_DOWNLOAD_LOGIN = "tts-download-login"
    TTS_DOWNLOAD = "tts-download"
    TTS_DOWNPOP_SUBSCRIPT = "tts-downpop-subscript"
    TTS_EXHAUSTED_SUBSCRIPT = "tts-exhausted-subscript"

    SOUND_GENERATE = "sound-generate"
    SOUND_PLAY = "sound-play"
    SOUND_GENPOP_LOGIN = "sound-genpop-login"
    SOUND_GENPOP_SUBSCRIPT = "sound-genpop-subscript"
    SOUND_DOWNLOAD_LOGIN = "sound-download-login"
    SOUND_DOWNLOAD = "sound-download"
    SOUND_DOWNPOP_SUBSCRIPT = "sound-downpop-subscript"
    SOUND_EXHAUSTED_SUBSCRIPT = "sound-exhausted-subscript"

    VOCAL_EXTRACT = "vocal-extract"
    VOCAL_EXTRACT_LOGIN = "vocal-extract-login"
    VOCAL_EXTRACT_SUBSCRIPT = "vocal-extract-subscript"
    VOCAL_DOWNLOAD = "vocal-download"
    VOCAL_DOWNLOAD_LOGIN = "vocal-download-login"
    VOCAL_DOWNLOAD_SUBSCRIPT = "vocal-download-subscript"
    VOCAL_EXTRACT_DOWNLOAD = "vocal-extract-download"
    VOCAL_EXTRACT_DOWNLOAD_LOGIN = "vocal-extract-download-login"
    VOCAL_EXTRACT_DOWNLOAD_SUBSCRIPT = "vocal-extract-download-subscript"
    VOCAL_EXTRACT_UPLOAD = "vocal-extract-upload"
    VOCAL_EXTRACT_EXHAUSTED_SUBSCRIPT = "vocal-extract-exhausted-subscript"

    VOCAL_REMOVE = "vocal-remove"
    VOCAL_REMOVE_LOGIN = "vocal-remove-login"
    VOCAL_REMOVE_SUBSCRIPT = "vocal-remove-subscript"
    VOCAL_REMOVE_DOWNLOAD = "vocal-remove-download"
    VOCAL_REMOVE_DOWNLOAD_LOGIN = "vocal-remove-download-login"
    VOCAL_REMOVE_DOWNLOAD_SUBSCRIPT = "vocal-remove-download-subscript"
    VOCAL_REMOVE_UPLOAD = "vocal-remove-upload"
    VOCAL_REMOVE_EXHAUSTED_SUBSCRIPT = "vocal-remove-exhausted-subscript"

    AUDIO_EXTRACT = "audio-extract"
    AUDIO_EXTRACT_LOGIN = "audio-extract-login"
    AUDIO_EXTRACT_SUBSCRIPT = "audio-extract-subscript"
    AUDIO_DOWNLOAD = "audio-download"
    AUDIO_DOWNLOAD_LOGIN = "audio-download-login"
    AUDIO_DOWNLOAD_SUBSCRIPT = "audio-download-subscript"
    AUDIO_EXTRACT_UPLOAD = "audio-extract-upload"
    AUDIO_EXTRACT_EXHAUSTED_SUBSCRIPT = "audio-extract-exhausted-subscript"

    @classmethod
    def get_all_values(cls):
        return [member.value for member in cls]

    @classmethod
    def is_valid_action(cls, action: str) -> bool:
        return action in cls.get_all_values()

    def __str__(self):
        return self.value
