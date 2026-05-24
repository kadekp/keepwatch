"""Segment recording helpers backed by ffmpeg."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import CropConfig, RecordingConfig
from .encoders import get_profile, vaapi_setup_args, validate_device
from .image_storage import MediaStorage


@dataclass(frozen=True)
class SegmentFile:
    path: Path
    started_at: datetime
    ended_at: datetime
    size_bytes: int


class SegmentRecorder:
    """Continuously records small TS segments for later clip assembly."""

    def __init__(
        self,
        camera_name: str,
        rtsp_url: str,
        recording: RecordingConfig,
        storage: MediaStorage,
        logger: logging.Logger,
        crop: CropConfig | None = None,
    ):
        self.camera_name = camera_name
        self.rtsp_url = rtsp_url
        self.recording = recording
        self.storage = storage
        self.logger = logger
        self.crop = crop or CropConfig()
        self.segment_dir = self.storage.segment_dir(camera_name)
        self._process: subprocess.Popen[str] | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        self.ensure_running()

    def ensure_running(self) -> None:
        if self._process and self._process.poll() is None:
            return

        if self._process and self._process.poll() is not None:
            self._log_process_exit(prefix="restarting")
            self.logger.warning(
                "[%s] Segment recorder exited with code %s, restarting.",
                self.camera_name,
                self._process.returncode,
            )

        self.segment_dir.mkdir(parents=True, exist_ok=True)
        command = self.build_command()
        self._last_error = None
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.5)
        if self._process.poll() is not None:
            self._log_process_exit(prefix="startup")
            raise RuntimeError(
                f"{self.camera_name} segment recorder failed to start: {self._last_error or 'unknown error'}"
            )
        self.logger.info("[%s] Started segment recorder.", self.camera_name)

    def stop(self) -> None:
        if not self._process:
            return

        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        else:
            self._log_process_exit(prefix="shutdown")
        self._process = None

    def is_running(self) -> bool:
        return bool(self._process and self._process.poll() is None)

    def last_error(self) -> str | None:
        return self._last_error

    def build_command(self) -> list[str]:
        pattern = self.segment_dir / f"{self.camera_name}_%Y%m%dT%H%M%S.ts"
        needs_reencode = not self.crop.is_full_frame()
        profile = get_profile(self.recording.encoder)

        if needs_reencode and profile.name == "vaapi":
            error = validate_device(profile, self.recording.vaapi_device)
            if error:
                raise RuntimeError(error)

        command: list[str] = [self.recording.ffmpeg_path]
        if needs_reencode and profile.name == "vaapi":
            command.extend(vaapi_setup_args(self.recording.vaapi_device))

        command.extend(
            [
                "-hide_banner",
                "-loglevel",
                "error",
                "-rtsp_transport",
                "tcp",
                "-i",
                self.rtsp_url,
                "-map",
                "0:v:0",
                "-an",
            ]
        )

        if self.crop.is_full_frame():
            command.extend(["-c", "copy"])
        else:
            crop_filter = (
                f"crop=iw*{self.crop.width}:ih*{self.crop.height}:iw*{self.crop.x}:ih*{self.crop.y}"
            )
            command.extend(
                profile.build_crop_args(
                    crop_filter=crop_filter,
                    segment_seconds=self.recording.segment_seconds,
                    vaapi_device=self.recording.vaapi_device,
                )
            )
        command.extend(
            [
                "-f",
                "segment",
                "-segment_time",
                str(self.recording.segment_seconds),
                "-strftime",
                "1",
                "-reset_timestamps",
                "1",
                "-segment_format",
                "mpegts",
                str(pattern),
            ]
        )
        return command

    def cleanup(self, now: datetime, keep_from: datetime | None = None) -> None:
        delete_before = now - timedelta(seconds=self.recording.segment_buffer_seconds)
        if keep_from:
            delete_before = min(delete_before, keep_from)

        protected_mtime = time.time() - max(self.recording.segment_seconds, 2)
        for segment in self.list_segments():
            if segment.ended_at >= delete_before:
                continue
            if segment.path.stat().st_mtime >= protected_mtime:
                continue
            try:
                segment.path.unlink()
            except FileNotFoundError:
                continue

    def segments_for_window(self, started_at: datetime, ended_at: datetime) -> list[SegmentFile]:
        segments = []
        for segment in self.list_segments():
            if segment.ended_at <= started_at:
                continue
            if segment.started_at >= ended_at:
                continue
            segments.append(segment)
        return segments

    def list_segments(self) -> list[SegmentFile]:
        segments: list[SegmentFile] = []
        for path in sorted(self.segment_dir.glob("*.ts")):
            segment = self._segment_from_path(path)
            if segment:
                segments.append(segment)
        return segments

    def build_clip(self, output_path: Path, segments: list[SegmentFile]) -> int:
        output_dir = output_path.parent
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        total_size = 0
        target_duration = max(
            1,
            max(
                int(round((segment.ended_at - segment.started_at).total_seconds()))
                for segment in segments
            ),
        )
        playlist_lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            f"#EXT-X-TARGETDURATION:{target_duration}",
            "#EXT-X-MEDIA-SEQUENCE:0",
        ]

        try:
            for index, segment in enumerate(segments):
                segment_name = f"segment_{index:03d}.ts"
                destination = output_dir / segment_name
                shutil.copyfile(segment.path, destination)
                total_size += destination.stat().st_size

                duration = max((segment.ended_at - segment.started_at).total_seconds(), 0.1)
                playlist_lines.append(f"#EXTINF:{duration:.3f},")
                playlist_lines.append(segment_name)

            playlist_lines.append("#EXT-X-ENDLIST")
            output_path.write_text("\n".join(playlist_lines) + "\n", encoding="utf-8")
            total_size += output_path.stat().st_size
        except Exception:
            shutil.rmtree(output_dir, ignore_errors=True)
            raise

        if not output_path.exists() or total_size <= 0:
            raise RuntimeError("Clip assembly produced no frames.")
        return total_size

    def _segment_from_path(self, path: Path) -> SegmentFile | None:
        stem = path.stem
        prefix = f"{self.camera_name}_"
        if not stem.startswith(prefix):
            return None
        timestamp_text = stem[len(prefix) :]
        try:
            started_at = datetime.strptime(timestamp_text, "%Y%m%dT%H%M%S")
        except ValueError:
            return None

        return SegmentFile(
            path=path,
            started_at=started_at,
            ended_at=started_at + timedelta(seconds=self.recording.segment_seconds),
            size_bytes=path.stat().st_size,
        )

    def _log_process_exit(self, prefix: str) -> None:
        if not self._process:
            return
        error_output = ""
        if self._process.stderr:
            try:
                error_output = self._process.stderr.read().strip()
            except Exception:
                error_output = ""
        if error_output:
            self._last_error = error_output
            self.logger.error(
                "[%s] Segment recorder %s error: %s", self.camera_name, prefix, error_output
            )
