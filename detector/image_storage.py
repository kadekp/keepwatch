"""Media storage helpers for clips, segments, thumbnails, and snapshots."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .config import StorageConfig

_RECALIBRATE_INTERVAL = 600  # full walk every 10 minutes


class MediaStorage:
    """Filesystem helpers for detector-managed media."""

    def __init__(self, config: StorageConfig):
        self.config = config
        self._ensure_directories()
        self._usage_bytes = self._walk_usage()
        self._usage_lock = threading.Lock()
        self._last_calibration = time.monotonic()

    def _ensure_directories(self) -> None:
        self.config.logs_dir.mkdir(parents=True, exist_ok=True)
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        for path in self.config.managed_paths:
            path.mkdir(parents=True, exist_ok=True)

    def save_frame(
        self, frame: np.ndarray, category: str, camera_name: str, timestamp: datetime
    ) -> str:
        base_dir = self._category_dir(category)
        relative_dir = Path(category) / camera_name / timestamp.strftime("%Y/%m/%d")
        full_dir = base_dir / camera_name / timestamp.strftime("%Y/%m/%d")
        full_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{timestamp.strftime('%H%M%S')}_{timestamp.microsecond // 1000:03d}.jpg"
        full_path = full_dir / filename
        cv2.imwrite(
            str(full_path),
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, self.config.jpeg_quality],
        )
        self._adjust_usage(full_path.stat().st_size)
        return str(relative_dir / filename)

    def reserve_clip_path(
        self, camera_name: str, started_at: datetime, ended_at: datetime
    ) -> tuple[Path, str]:
        event_dir_name = (
            f"{started_at.strftime('%H%M%S')}_{ended_at.strftime('%H%M%S')}_{camera_name}"
        )
        relative_dir = (
            Path("clips") / camera_name / started_at.strftime("%Y/%m/%d") / event_dir_name
        )
        full_dir = self.config.media_root / relative_dir
        full_dir.mkdir(parents=True, exist_ok=True)

        relative_path = relative_dir / "index.m3u8"
        return self.config.media_root / relative_path, str(relative_path)

    def segment_dir(self, camera_name: str) -> Path:
        path = self.config.segments_dir / camera_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def temp_file(self, name: str) -> Path:
        self.config.temp_dir.mkdir(parents=True, exist_ok=True)
        return self.config.temp_dir / name

    def absolute_path(self, relative_path: str | Path) -> Path:
        return self.config.media_root / Path(relative_path)

    def delete_paths(self, relative_paths: Iterable[str]) -> None:
        for relative_path in relative_paths:
            path = self.absolute_path(relative_path)
            if not path.exists():
                continue

            if path.is_dir():
                freed = self._tree_size(path)
                shutil.rmtree(path, ignore_errors=True)
                self._adjust_usage(-freed)
                self._remove_empty_parents(path.parent)
                continue

            if path.suffix == ".m3u8":
                event_dir = path.parent
                freed = self._tree_size(event_dir)
                shutil.rmtree(event_dir, ignore_errors=True)
                self._adjust_usage(-freed)
                self._remove_empty_parents(event_dir.parent)
                continue

            freed = path.stat().st_size
            path.unlink()
            self._adjust_usage(-freed)
            self._remove_empty_parents(path.parent)

    def managed_usage_bytes(self) -> int:
        now = time.monotonic()
        if now - self._last_calibration >= _RECALIBRATE_INTERVAL:
            self._usage_bytes = self._walk_usage()
            self._last_calibration = now
        return max(0, self._usage_bytes)

    def _walk_usage(self) -> int:
        total = 0
        for root in self.config.managed_paths:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    total += path.stat().st_size
        return total

    def _adjust_usage(self, delta: int) -> None:
        with self._usage_lock:
            self._usage_bytes += delta

    @staticmethod
    def _tree_size(root: Path) -> int:
        total = 0
        if not root.exists():
            return 0
        for path in root.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total

    def media_url(self, relative_path: str | None) -> str | None:
        if not relative_path:
            return None
        return f"/media/{Path(relative_path).as_posix()}"

    def _category_dir(self, category: str) -> Path:
        if category == "thumbnails":
            return self.config.thumbnails_dir
        if category == "snapshots":
            return self.config.snapshots_dir
        raise ValueError(f"Unsupported image category: {category}")

    def _remove_empty_parents(self, start_path: Path) -> None:
        current = start_path
        managed_roots = set(self.config.managed_paths)
        while current not in managed_roots and current != self.config.media_root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
