from __future__ import annotations

import logging
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from detector.config import (
    ApiConfig,
    AppConfig,
    CameraConfig,
    CropConfig,
    ModelConfig,
    RecordingConfig,
    StorageConfig,
)
from detector.database import DetectionDatabase
from detector.frame_processing import prepare_frame
from detector.image_storage import MediaStorage
from detector.service import CameraWorker


class DummyDetector:
    def detect(self, frame):
        return []


class CameraPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.storage = MediaStorage(
            StorageConfig(
                project_root=root,
                db_path=root / "data" / "detections.db",
                logs_dir=root / "logs",
                media_root=root / "media",
            )
        )
        self.database = DetectionDatabase(self.storage.config.db_path)
        self.camera = CameraConfig(
            name="icsee",
            display_name="iCSee Camera",
            detect_rtsp_url="rtsp://127.0.0.1:8554/icsee?video=h264",
            record_rtsp_url="rtsp://127.0.0.1:8554/icsee?video=h264",
            frame_width=640,
            frame_height=640,
            crop=CropConfig(x=0.0, y=0.5, width=1.0, height=0.5),
        )
        self.config = AppConfig(
            config_path=root / "detector.yaml",
            cameras=[self.camera],
            model=ModelConfig(model_path=root / "yolov8n.pt"),
            recording=RecordingConfig(),
            storage=self.storage.config,
            api=ApiConfig(),
        )
        logger = logging.getLogger(f"camera-pipeline-{id(self)}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        self.worker = CameraWorker(
            camera=self.camera,
            config=self.config,
            database=self.database,
            storage=self.storage,
            shared_detector=DummyDetector(),
            detector_lock=threading.Lock(),
            retention_callback=lambda: None,
            logger=logger,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_top_half_motion_is_ignored_but_bottom_half_creates_cropped_event(self) -> None:
        base_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        top_motion = base_frame.copy()
        bottom_motion = base_frame.copy()
        cv2.rectangle(top_motion, (100, 80), (1180, 220), (255, 255, 255), -1)
        cv2.rectangle(bottom_motion, (100, 520), (1180, 680), (255, 255, 255), -1)

        baseline_prepared = prepare_frame(
            base_frame,
            crop=self.camera.crop,
            target_size=(self.camera.frame_width, self.camera.frame_height),
        )
        top_prepared = prepare_frame(
            top_motion,
            crop=self.camera.crop,
            target_size=(self.camera.frame_width, self.camera.frame_height),
        )
        bottom_prepared = prepare_frame(
            bottom_motion,
            crop=self.camera.crop,
            target_size=(self.camera.frame_width, self.camera.frame_height),
        )

        now = datetime.now()
        for _ in range(8):
            self.worker.motion_detector.detect_motion(baseline_prepared)

        for _ in range(4):
            self.worker._process_frame(top_prepared, now)
        self.assertIsNone(self.worker._active_event)

        for _ in range(4):
            self.worker._process_frame(bottom_prepared, now)

        self.assertIsNotNone(self.worker._active_event)
        event = self.database.get_event(self.worker._active_event.event_id)
        self.assertIsNotNone(event)
        self.assertTrue(event.metadata["crop"]["active"])

        thumbnail_path = self.storage.absolute_path(event.thumbnail_path)
        self.assertTrue(thumbnail_path.exists())

        thumbnail = cv2.imread(str(thumbnail_path))
        self.assertEqual(thumbnail.shape[1], 640)
        self.assertEqual(thumbnail.shape[0], 180)


if __name__ == "__main__":
    unittest.main()
