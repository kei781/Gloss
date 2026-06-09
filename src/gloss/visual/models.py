from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def parse(cls, value: str) -> "Rect":
        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 4:
            raise ValueError("Rect must be X,Y,WIDTH,HEIGHT.")
        x, y, width, height = (int(part) for part in parts)
        if width <= 0 or height <= 0:
            raise ValueError("Rect width and height must be greater than zero.")
        return cls(x=x, y=y, width=width, height=height)


@dataclass(frozen=True)
class CaptureResult:
    rect: Rect
    image_path: Path
    backend: str
    elapsed_s: float


@dataclass(frozen=True)
class VisualTranslation:
    translated_text: str
    source_text: str | None
    capture: CaptureResult | None
