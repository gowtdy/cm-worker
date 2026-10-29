import importlib

# engine 名 → (模块, 工厂)。懒加载，避免启动时 import 未安装的 funasr/parakeet 依赖。
_ENGINE_MODULES = {
    "whisper": ("transcriber.whisper_engine", "WhisperEngine"),
    # future:
    # "funasr":   ("transcriber.funasr_engine", "FunasrEngine"),
    # "parakeet": ("transcriber.parakeet_engine", "ParakeetEngine"),
}

_cache = {}


def get_engine(name: str):
    if name not in _ENGINE_MODULES:
        raise KeyError(f"unknown transcribe engine: {name}")
    if name not in _cache:
        module_name, cls_name = _ENGINE_MODULES[name]
        module = importlib.import_module(module_name)
        cls = getattr(module, cls_name)
        if name == "whisper":
            from config.model_conf import get_whisper_model_path, get_whisper_model_name
            _cache[name] = cls(get_whisper_model_path(), get_whisper_model_name())
        else:
            _cache[name] = cls()
    return _cache[name]