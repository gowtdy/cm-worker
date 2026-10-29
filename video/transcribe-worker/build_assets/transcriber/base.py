import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Word:
    text: str
    start: float
    end: float
    probability: Optional[float] = None


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)


@dataclass
class TranscribeResult:
    schema_version: str = "1.0"
    engine: str = ""
    model: str = ""
    language: str = ""
    language_probability: Optional[float] = None
    duration: float = 0.0
    text: str = ""
    segments: List[Segment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "engine": self.engine,
            "model": self.model,
            "language": self.language,
            "language_probability": self.language_probability,
            "duration": self.duration,
            "text": self.text,
            "segments": [
                {
                    "id": s.id,
                    "start": s.start,
                    "end": s.end,
                    "text": s.text,
                    "words": [
                        {"word": w.text, "start": w.start, "end": w.end,
                         "probability": w.probability}
                        for w in s.words
                    ],
                }
                for s in self.segments
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class TranscribeEngine(ABC):
    @abstractmethod
    def transcribe(self, audio_path: str, language: Optional[str] = None) -> TranscribeResult:
        ...


def _norm_word(raw) -> Word:
    return Word(text=(raw.word or "").strip(), start=float(raw.start),
                end=float(raw.end),
                probability=(float(raw.probability) if raw.probability is not None else None))


def _norm_segment(seg_id, raw) -> Segment:
    words = []
    if raw.words:
        words = [_norm_word(w) for w in raw.words]
    return Segment(id=seg_id, start=float(raw.start), end=float(raw.end),
                   text=(raw.text or "").strip(), words=words)


def segments_to_result(segments, info, *, engine: str, model: str) -> TranscribeResult:
    """把 ASR 引擎的 duck-typed segments/info 归一为规范 TranscribeResult。"""
    norm_segments = [_norm_segment(i, s) for i, s in enumerate(segments)]
    full_text = " ".join(s.text for s in norm_segments if s.text)
    return TranscribeResult(
        engine=engine,
        model=model,
        language=(info.language or ""),
        language_probability=(float(info.language_probability)
                              if getattr(info, "language_probability", None) is not None
                              else None),
        duration=float(info.duration or 0.0),
        text=full_text,
        segments=norm_segments,
    )