from typing import Optional

from transcriber.base import TranscribeResult, TranscribeEngine, segments_to_result


class WhisperEngine(TranscribeEngine):
    def __init__(self, model_path: str, model_name: str,
                 device: str = "auto", compute_type: str = "float16"):
        self.model_path = model_path
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._load()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # 懒加载，避免未装依赖时 import 失败
            self._model = WhisperModel(
                self.model_path, device=self.device, compute_type=self.compute_type)
        return self._model

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> TranscribeResult:
        model = self._load()
        lang = language if language not in (None, "", "auto") else None
        segments, info = model.transcribe(
            audio_path, beam_size=5, word_timestamps=True,
            vad_filter=True, vad_parameters=dict(min_silence_duration_ms=500),
            language=lang,
        )
        return segments_to_result(list(segments), info, engine="whisper",
                                  model=self.model_name)