"""Frame crop and resize helpers used by capture, storage, and tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np


class CropLike(Protocol):
    x: float
    y: float
    width: float
    height: float

    def is_full_frame(self) -> bool: ...


@dataclass(frozen=True)
class PixelCrop:
    x: int
    y: int
    width: int
    height: int


def resolve_crop(frame: np.ndarray, crop: CropLike | None) -> PixelCrop:
    frame_height, frame_width = frame.shape[:2]
    if not crop or crop.is_full_frame():
        return PixelCrop(x=0, y=0, width=frame_width, height=frame_height)

    x = int(round(frame_width * crop.x))
    y = int(round(frame_height * crop.y))
    width = int(round(frame_width * crop.width))
    height = int(round(frame_height * crop.height))

    width = max(1, min(width, frame_width - x))
    height = max(1, min(height, frame_height - y))
    return PixelCrop(x=x, y=y, width=width, height=height)


def crop_frame(frame: np.ndarray, crop: CropLike | None) -> np.ndarray:
    rect = resolve_crop(frame, crop)
    return frame[rect.y : rect.y + rect.height, rect.x : rect.x + rect.width].copy()


def resize_to_fit(frame: np.ndarray, target_size: tuple[int, int]) -> np.ndarray:
    target_width, target_height = target_size
    if target_width <= 0 or target_height <= 0:
        return frame

    frame_height, frame_width = frame.shape[:2]
    scale = min(target_width / frame_width, target_height / frame_height)
    if scale >= 1.0:
        return frame

    resized_width = max(1, int(round(frame_width * scale)))
    resized_height = max(1, int(round(frame_height * scale)))
    return cv2.resize(frame, (resized_width, resized_height), interpolation=cv2.INTER_AREA)


def prepare_frame(
    frame: np.ndarray,
    crop: CropLike | None = None,
    target_size: tuple[int, int] | None = None,
) -> np.ndarray:
    processed = crop_frame(frame, crop)
    if target_size:
        processed = resize_to_fit(processed, target_size)
    return processed
