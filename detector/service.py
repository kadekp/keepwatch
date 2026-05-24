"""Detection supervisor for multi-camera motion recording and person detection."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .config import AppConfig, CameraConfig
from .database import DetectionDatabase, DetectionRecord, EventRecord
from .image_storage import MediaStorage
from .motion_detector import MotionDetector
from .person_detector import PersonDetector
from .recording import SegmentRecorder
from .stream_capture import RTSPCapture


@dataclass
class EventSession:
    event_id: int
    started_at: datetime
    last_motion_at: datetime
    thumbnail_path: str
    snapshot_path: str | None = None
    best_confidence: float | None = None
    has_person: bool = False


class CameraWorker(threading.Thread):
    """Runs motion detection, clip assembly, and person detection for one camera."""

    def __init__(
        self,
        camera: CameraConfig,
        config: AppConfig,
        database: DetectionDatabase,
        storage: MediaStorage,
        shared_detector: PersonDetector,
        detector_lock: threading.Lock,
        retention_callback,
        logger: logging.Logger,
    ):
        super().__init__(daemon=True, name=f"camera-worker-{camera.name}")
        self.camera = camera
        self.config = config
        self.database = database
        self.storage = storage
        self.shared_detector = shared_detector
        self.detector_lock = detector_lock
        self.retention_callback = retention_callback
        self.logger = logger

        self.capture = RTSPCapture(
            camera.detect_rtsp_url,
            target_fps=camera.capture_fps,
            frame_size=(camera.frame_width, camera.frame_height),
            crop=camera.crop,
        )
        self.motion_detector = MotionDetector(
            threshold=camera.motion_threshold,
            min_area=camera.motion_min_area,
            blur_size=camera.motion_blur_size,
        )
        self.recorder = SegmentRecorder(
            camera_name=camera.name,
            rtsp_url=camera.record_rtsp_url,
            recording=config.recording,
            storage=storage,
            logger=logger,
            crop=camera.crop,
        )

        self._stop_event = threading.Event()
        self._active_event: EventSession | None = None
        self._last_cleanup_monotonic = 0.0
        self._last_inference_monotonic = 0.0
        self._last_detection_monotonic = 0.0

    def run(self) -> None:
        self.logger.info("[%s] Starting camera worker.", self.camera.name)
        self.capture.start()
        try:
            self.recorder.start()
        except Exception:
            self.logger.exception("[%s] Recorder failed to start.", self.camera.name)

        try:
            while not self._stop_event.is_set():
                try:
                    self.recorder.ensure_running()
                except Exception:
                    self.logger.exception("[%s] Recorder health check failed.", self.camera.name)
                    time.sleep(1.0)
                now = datetime.now()
                frame = self.capture.get_frame(timeout=1.0)

                if frame is not None:
                    self._process_frame(frame, now)
                else:
                    time.sleep(0.1)

                self._maybe_finalize_event(now)
                self._maintenance(now)
        finally:
            if self._active_event:
                self._finalize_event(datetime.now())
            self.capture.stop()
            self.recorder.stop()
            self.logger.info("[%s] Camera worker stopped.", self.camera.name)

    def stop(self) -> None:
        self._stop_event.set()

    def status(self) -> dict[str, object]:
        frame_size = self.capture.last_frame_size()
        return {
            "camera_name": self.camera.name,
            "display_name": self.camera.display_name,
            "stream_connected": self.capture.is_connected(),
            "recorder_running": self.recorder.is_running(),
            "recorder_error": self.recorder.last_error(),
            "event_active": self._active_event is not None,
            "crop": self.camera.crop.as_dict(),
            "frame_width": frame_size[0] if frame_size else None,
            "frame_height": frame_size[1] if frame_size else None,
        }

    def _process_frame(self, frame, now: datetime) -> None:
        motion_detected, _, _ = self.motion_detector.detect_motion(frame)
        if not motion_detected:
            return

        event = self._ensure_event(frame, now)
        event.last_motion_at = now
        self._maybe_detect_person(frame, now)

    def _ensure_event(self, frame, now: datetime) -> EventSession:
        if self._active_event:
            return self._active_event

        thumbnail_path = self.storage.save_frame(
            frame=frame,
            category="thumbnails",
            camera_name=self.camera.name,
            timestamp=now,
        )
        event_id = self.database.create_event(
            camera_name=self.camera.name,
            started_at=now,
            thumbnail_path=thumbnail_path,
            metadata=self._frame_metadata(frame),
        )
        self._active_event = EventSession(
            event_id=event_id,
            started_at=now,
            last_motion_at=now,
            thumbnail_path=thumbnail_path,
        )
        self.logger.info("[%s] Started motion event %s.", self.camera.name, event_id)
        return self._active_event

    def _maybe_detect_person(self, frame, now: datetime) -> None:
        if not self._active_event:
            return

        inference_interval = 1.0 / max(self.camera.inference_fps, 0.1)
        monotonic_now = time.monotonic()
        if monotonic_now - self._last_inference_monotonic < inference_interval:
            return

        self._last_inference_monotonic = monotonic_now
        with self.detector_lock:
            detections = self.shared_detector.detect(frame)

        if not detections:
            return

        if monotonic_now - self._last_detection_monotonic < self.camera.cooldown_seconds:
            return

        snapshot_path = self.storage.save_frame(
            frame=frame,
            category="snapshots",
            camera_name=self.camera.name,
            timestamp=now,
        )
        best_confidence = max(detection.confidence for detection in detections)
        self.database.mark_event_detection(
            event_id=self._active_event.event_id,
            confidence=best_confidence,
            snapshot_path=snapshot_path,
        )

        for detection in detections:
            self.database.insert_detection(
                DetectionRecord(
                    id=None,
                    event_id=self._active_event.event_id,
                    timestamp=now,
                    camera_name=self.camera.name,
                    confidence=detection.confidence,
                    bbox_x1=detection.bbox[0],
                    bbox_y1=detection.bbox[1],
                    bbox_x2=detection.bbox[2],
                    bbox_y2=detection.bbox[3],
                    image_path=snapshot_path,
                    metadata=self._frame_metadata(frame),
                )
            )

        self._active_event.has_person = True
        if (
            self._active_event.best_confidence is None
            or best_confidence >= self._active_event.best_confidence
        ):
            self._active_event.best_confidence = best_confidence
            self._active_event.snapshot_path = snapshot_path

        self._last_detection_monotonic = monotonic_now
        self.logger.info(
            "[%s] Person detected in event %s at confidence %.2f.",
            self.camera.name,
            self._active_event.event_id,
            best_confidence,
        )

    def _maybe_finalize_event(self, now: datetime) -> None:
        if not self._active_event:
            return

        event_end = self._active_event.last_motion_at + timedelta(
            seconds=self.config.recording.post_roll_seconds
        )
        if now < event_end:
            return

        self._finalize_event(event_end)

    def _finalize_event(self, ended_at: datetime) -> None:
        if not self._active_event:
            return

        active_event = self._active_event
        self._active_event = None

        clip_start = active_event.started_at - timedelta(
            seconds=self.config.recording.pre_roll_seconds
        )
        segments = self.recorder.segments_for_window(clip_start, ended_at)
        if not segments:
            self.logger.warning(
                "[%s] No segments found for event %s, discarding.",
                self.camera.name,
                active_event.event_id,
            )
            self.storage.delete_paths(self.database.get_event_file_paths(active_event.event_id))
            self.database.delete_event(active_event.event_id)
            return

        output_path, relative_clip_path = self.storage.reserve_clip_path(
            camera_name=self.camera.name,
            started_at=active_event.started_at,
            ended_at=ended_at,
        )
        try:
            size_bytes = self.recorder.build_clip(output_path, segments)
        except Exception:
            self.logger.exception(
                "[%s] Failed to build clip for event %s.",
                self.camera.name,
                active_event.event_id,
            )
            self.storage.delete_paths(self.database.get_event_file_paths(active_event.event_id))
            self.database.delete_event(active_event.event_id)
            self.storage.delete_paths({relative_clip_path})
            return

        self.database.finalize_event(
            event_id=active_event.event_id,
            ended_at=ended_at,
            clip_path=relative_clip_path,
            thumbnail_path=active_event.thumbnail_path,
            snapshot_path=active_event.snapshot_path,
            size_bytes=size_bytes,
        )
        self.logger.info(
            "[%s] Finalized event %s with clip %s.",
            self.camera.name,
            active_event.event_id,
            relative_clip_path,
        )
        self.retention_callback()

    def _maintenance(self, now: datetime) -> None:
        monotonic_now = time.monotonic()
        if monotonic_now - self._last_cleanup_monotonic < 5:
            return

        keep_from = None
        if self._active_event:
            keep_from = self._active_event.started_at - timedelta(
                seconds=self.config.recording.pre_roll_seconds
            )
        self.recorder.cleanup(now=now, keep_from=keep_from)
        self._last_cleanup_monotonic = monotonic_now

    def _frame_metadata(self, frame) -> dict[str, Any]:
        return {
            "crop": self.camera.crop.as_dict(),
            "frame_width": int(frame.shape[1]),
            "frame_height": int(frame.shape[0]),
        }


class DetectorSupervisor:
    """Owns all detector workers, SQLite state, and retention management."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.logger = self._setup_logging()
        self.storage = MediaStorage(config.storage)
        self.database = DetectionDatabase(config.storage.db_path)
        self.shared_detector = PersonDetector(
            model_name=str(config.model.model_path),
            confidence_threshold=min(camera.confidence_threshold for camera in config.cameras),
            use_mps=config.model.use_mps,
        )
        self.detector_lock = threading.Lock()
        self.retention_lock = threading.Lock()
        self._workers = [
            CameraWorker(
                camera=camera,
                config=config,
                database=self.database,
                storage=self.storage,
                shared_detector=self.shared_detector,
                detector_lock=self.detector_lock,
                retention_callback=self.apply_retention,
                logger=self.logger,
            )
            for camera in config.cameras
        ]
        self._started = False
        self._retention_thread = threading.Thread(
            target=self._retention_loop,
            daemon=True,
            name="retention-loop",
        )
        self._retention_stop = threading.Event()

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for worker in self._workers:
            worker.start()
        self._retention_thread.start()
        self.logger.info("Detector supervisor started with %s cameras.", len(self._workers))

    def stop(self) -> None:
        if not self._started:
            return
        self._retention_stop.set()
        for worker in self._workers:
            worker.stop()
        for worker in self._workers:
            worker.join(timeout=10)
        self._retention_thread.join(timeout=5)
        self._started = False
        self.logger.info("Detector supervisor stopped.")

    def list_events(
        self,
        limit: int = 20,
        camera_name: str | None = None,
        kind: str = "all",
    ) -> list[EventRecord]:
        return self.database.list_events(limit=limit, camera_name=camera_name, kind=kind)

    def storage_summary(self) -> dict[str, object]:
        usage_bytes = self.storage.managed_usage_bytes()
        return {
            "usage_bytes": usage_bytes,
            "usage_gb": round(usage_bytes / (1024**3), 2),
            "limit_bytes": self.config.storage.max_bytes,
            "limit_gb": round(self.config.storage.max_bytes / (1024**3), 2),
            "low_water_bytes": self.config.storage.low_water_bytes,
            "event_count": self.database.count_events(),
        }

    def health(self) -> dict[str, object]:
        return {
            "status": "ok",
            "cameras": [worker.status() for worker in self._workers],
            "storage": self.storage_summary(),
        }

    def camera_display_name(self, camera_name: str) -> str:
        for camera in self.config.cameras:
            if camera.name == camera_name:
                return camera.display_name
        return camera_name

    def apply_retention(self) -> None:
        with self.retention_lock:
            usage_bytes = self.storage.managed_usage_bytes()
            if usage_bytes <= self.config.storage.max_bytes:
                return

            for event_id in self.database.list_oldest_event_ids():
                file_paths = self.database.get_event_file_paths(event_id)
                self.storage.delete_paths(file_paths)
                self.database.delete_event(event_id)
                usage_bytes = self.storage.managed_usage_bytes()
                self.logger.info(
                    "Retention removed event %s, usage now %.2f GB.",
                    event_id,
                    usage_bytes / (1024**3),
                )
                if usage_bytes <= self.config.storage.low_water_bytes:
                    break

    def _retention_loop(self) -> None:
        while not self._retention_stop.is_set():
            try:
                self.apply_retention()
            except Exception:
                self.logger.exception("Retention loop failed.")
            self._retention_stop.wait(60)

    def _setup_logging(self) -> logging.Logger:
        logger = logging.getLogger("detector")
        logger.setLevel(getattr(logging, self.config.log_level, logging.INFO))
        logger.handlers.clear()
        logger.propagate = False

        self.config.storage.logs_dir.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(self.config.storage.logs_dir / "detector.log")
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(stream_handler)

        return logger
