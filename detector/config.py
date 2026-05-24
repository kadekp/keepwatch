"""Configuration loading for the CCTV detection service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

GIGABYTE = 1024**3


@dataclass(frozen=True)
class CropConfig:
    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0

    def is_full_frame(self) -> bool:
        return self.x <= 0.0 and self.y <= 0.0 and self.width >= 1.0 and self.height >= 1.0

    def as_dict(self) -> dict[str, float | bool]:
        return {
            "active": not self.is_full_frame(),
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class CameraConfig:
    name: str
    display_name: str
    detect_rtsp_url: str
    record_rtsp_url: str
    capture_fps: float = 4.0
    inference_fps: float = 2.0
    frame_width: int = 640
    frame_height: int = 640
    motion_threshold: int = 25
    motion_min_area: int = 5000
    motion_blur_size: int = 21
    confidence_threshold: float = 0.5
    cooldown_seconds: float = 2.0
    crop: CropConfig = CropConfig()


@dataclass(frozen=True)
class ModelConfig:
    model_path: Path
    use_mps: bool = False


@dataclass(frozen=True)
class RecordingConfig:
    ffmpeg_path: str = "ffmpeg"
    segment_seconds: int = 5
    pre_roll_seconds: int = 10
    post_roll_seconds: int = 20
    segment_buffer_seconds: int = 180
    # Encoder used when a crop is configured (which forces re-encoding).
    # One of: "software" (libx264, default), "vaapi", "nvenc", "videotoolbox".
    encoder: str = "software"
    # Render device for the vaapi encoder. Ignored for other encoders.
    vaapi_device: str = "/dev/dri/renderD128"


@dataclass(frozen=True)
class StorageConfig:
    project_root: Path
    db_path: Path
    logs_dir: Path
    media_root: Path
    jpeg_quality: int = 85
    max_bytes: int = 50 * GIGABYTE
    low_water_bytes: int = 45 * GIGABYTE

    @property
    def clips_dir(self) -> Path:
        return self.media_root / "clips"

    @property
    def thumbnails_dir(self) -> Path:
        return self.media_root / "thumbnails"

    @property
    def snapshots_dir(self) -> Path:
        return self.media_root / "snapshots"

    @property
    def segments_dir(self) -> Path:
        return self.media_root / "segments"

    @property
    def temp_dir(self) -> Path:
        return self.media_root / "tmp"

    @property
    def managed_paths(self) -> list[Path]:
        return [
            self.clips_dir,
            self.thumbnails_dir,
            self.snapshots_dir,
            self.segments_dir,
            self.temp_dir,
        ]


@dataclass(frozen=True)
class ApiConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origin_regex: str = r"^https?://[^/]+:1984$"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@dataclass(frozen=True)
class AppConfig:
    config_path: Path
    cameras: list[CameraConfig]
    model: ModelConfig
    recording: RecordingConfig
    storage: StorageConfig
    api: ApiConfig
    log_level: str = "INFO"


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    config_dir = config_path.parent
    project_root = config_dir

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    storage_raw = raw.get("storage", {})
    recording_raw = raw.get("recording", {})
    api_raw = raw.get("api", {})
    model_raw = raw.get("model", {})
    cameras_raw = raw.get("cameras", [])

    if not cameras_raw:
        raise ValueError("Config must define at least one camera.")

    storage = StorageConfig(
        project_root=project_root,
        db_path=_resolve_path(config_dir, storage_raw.get("db_path", "data/detections.db")),
        logs_dir=_resolve_path(config_dir, storage_raw.get("logs_dir", "logs")),
        media_root=_resolve_path(config_dir, storage_raw.get("media_root", "media")),
        jpeg_quality=int(storage_raw.get("jpeg_quality", 85)),
        max_bytes=int(storage_raw.get("max_bytes", 50 * GIGABYTE)),
        low_water_bytes=int(storage_raw.get("low_water_bytes", 45 * GIGABYTE)),
    )
    if storage.low_water_bytes >= storage.max_bytes:
        raise ValueError("storage.low_water_bytes must be lower than storage.max_bytes")

    recording = RecordingConfig(
        ffmpeg_path=str(os.environ.get("FFMPEG_PATH", recording_raw.get("ffmpeg_path", "ffmpeg"))),
        segment_seconds=int(recording_raw.get("segment_seconds", 5)),
        pre_roll_seconds=int(recording_raw.get("pre_roll_seconds", 10)),
        post_roll_seconds=int(recording_raw.get("post_roll_seconds", 20)),
        segment_buffer_seconds=int(recording_raw.get("segment_buffer_seconds", 180)),
        encoder=str(recording_raw.get("encoder", "software")).strip().lower(),
        vaapi_device=str(recording_raw.get("vaapi_device", "/dev/dri/renderD128")),
    )
    if recording.segment_seconds <= 0:
        raise ValueError("recording.segment_seconds must be positive")

    api = ApiConfig(
        host=str(api_raw.get("host", "0.0.0.0")),
        port=int(api_raw.get("port", 8000)),
        cors_origin_regex=str(api_raw.get("cors_origin_regex", r"^https?://[^/]+:1984$")),
    )

    model = ModelConfig(
        model_path=_resolve_path(config_dir, model_raw.get("model_path", "yolov8n.pt")),
        use_mps=bool(model_raw.get("use_mps", False)),
    )

    cameras = [_load_camera_config(entry) for entry in cameras_raw]
    names = [camera.name for camera in cameras]
    if len(names) != len(set(names)):
        raise ValueError("Camera names must be unique.")

    return AppConfig(
        config_path=config_path,
        cameras=cameras,
        model=model,
        recording=recording,
        storage=storage,
        api=api,
        log_level=str(raw.get("log_level", "INFO")).upper(),
    )


def _load_camera_config(entry: dict[str, Any]) -> CameraConfig:
    required = ["name", "display_name", "detect_rtsp_url", "record_rtsp_url"]
    missing = [key for key in required if key not in entry]
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(f"Camera config missing required keys: {missing_text}")

    camera_name = str(entry["name"])
    crop_raw = entry.get("crop")

    return CameraConfig(
        name=camera_name,
        display_name=str(entry["display_name"]),
        detect_rtsp_url=str(entry["detect_rtsp_url"]),
        record_rtsp_url=str(entry["record_rtsp_url"]),
        capture_fps=float(entry.get("capture_fps", 4.0)),
        inference_fps=float(entry.get("inference_fps", 2.0)),
        frame_width=int(entry.get("frame_width", 640)),
        frame_height=int(entry.get("frame_height", 640)),
        motion_threshold=int(entry.get("motion_threshold", 25)),
        motion_min_area=int(entry.get("motion_min_area", 5000)),
        motion_blur_size=int(entry.get("motion_blur_size", 21)),
        confidence_threshold=float(entry.get("confidence_threshold", 0.5)),
        cooldown_seconds=float(entry.get("cooldown_seconds", 2.0)),
        crop=_load_crop_config(crop_raw),
    )


def _resolve_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _load_crop_config(value: Any) -> CropConfig:
    if value is None:
        return CropConfig()
    if not isinstance(value, dict):
        raise ValueError("camera.crop must be a mapping with x, y, width, and height")

    crop = CropConfig(
        x=float(value.get("x", 0.0)),
        y=float(value.get("y", 0.0)),
        width=float(value.get("width", 1.0)),
        height=float(value.get("height", 1.0)),
    )
    if crop.width <= 0.0 or crop.height <= 0.0:
        raise ValueError("camera.crop width and height must be positive")
    if crop.x < 0.0 or crop.y < 0.0:
        raise ValueError("camera.crop x and y must be >= 0")
    if crop.x + crop.width > 1.0 or crop.y + crop.height > 1.0:
        raise ValueError("camera.crop must stay within the source frame")
    return crop
