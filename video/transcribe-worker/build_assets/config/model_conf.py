import os


def get_whisper_model_path() -> str:
    return os.getenv("WHISPER_MODEL_PATH", "/app/models/whisper-model")


def get_whisper_model_name() -> str:
    return os.getenv("WHISPER_MODEL_NAME", "large-v3-turbo")