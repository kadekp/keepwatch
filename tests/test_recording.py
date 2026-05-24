from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from detector.config import CropConfig, RecordingConfig, StorageConfig
from detector.image_storage import MediaStorage
from detector.recording import SegmentRecorder


class RecordingCommandTests(unittest.TestCase):
    """Encoder-matrix tests for SegmentRecorder.build_command."""

    def build_recorder(
        self,
        crop: CropConfig,
        encoder: str = "software",
    ) -> SegmentRecorder:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        storage = MediaStorage(
            StorageConfig(
                project_root=root,
                db_path=root / "data" / "detections.db",
                logs_dir=root / "logs",
                media_root=root / "media",
            )
        )
        logger = logging.getLogger(f"recording-test-{id(root)}-{encoder}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        return SegmentRecorder(
            camera_name="cam0",
            rtsp_url="rtsp://127.0.0.1:8554/cam0?video=h264",
            recording=RecordingConfig(
                ffmpeg_path="ffmpeg",
                segment_seconds=5,
                encoder=encoder,
            ),
            storage=storage,
            logger=logger,
            crop=crop,
        )

    def test_uncropped_uses_stream_copy_regardless_of_encoder(self) -> None:
        for encoder in ("software", "vaapi", "nvenc", "videotoolbox"):
            with self.subTest(encoder=encoder):
                recorder = self.build_recorder(CropConfig(), encoder=encoder)
                command = recorder.build_command()
                self.assertIn("-c", command)
                self.assertIn("copy", command)
                self.assertNotIn("-vf", command)
                self.assertNotIn("-vaapi_device", command)

    def test_software_encoder_cropped_uses_libx264(self) -> None:
        recorder = self.build_recorder(
            CropConfig(x=0.0, y=0.5, width=1.0, height=0.5),
            encoder="software",
        )
        command = recorder.build_command()

        self.assertIn("-vf", command)
        vf_index = command.index("-vf")
        self.assertEqual(command[vf_index + 1], "crop=iw*1.0:ih*0.5:iw*0.0:ih*0.5")
        self.assertIn("libx264", command)
        self.assertNotIn("h264_vaapi", command)
        self.assertNotIn("-vaapi_device", command)

    def test_vaapi_encoder_cropped_uses_h264_vaapi(self) -> None:
        recorder = self.build_recorder(
            CropConfig(x=0.0, y=0.5, width=1.0, height=0.5),
            encoder="vaapi",
        )
        # build_command performs device validation; skip the assertion if
        # /dev/dri/renderD128 is not present on this host (e.g. macOS, CI).
        try:
            command = recorder.build_command()
        except RuntimeError as exc:
            self.skipTest(f"VA-API device unavailable on this host: {exc}")

        self.assertIn("-vaapi_device", command)
        self.assertIn("-vf", command)
        vf_index = command.index("-vf")
        self.assertIn("format=nv12,hwupload", command[vf_index + 1])
        self.assertIn("h264_vaapi", command)
        self.assertNotIn("libx264", command)

    def test_nvenc_encoder_cropped_uses_h264_nvenc(self) -> None:
        recorder = self.build_recorder(
            CropConfig(x=0.0, y=0.5, width=1.0, height=0.5),
            encoder="nvenc",
        )
        command = recorder.build_command()
        self.assertIn("h264_nvenc", command)
        self.assertNotIn("libx264", command)
        self.assertNotIn("h264_vaapi", command)

    def test_videotoolbox_encoder_cropped_uses_videotoolbox(self) -> None:
        recorder = self.build_recorder(
            CropConfig(x=0.0, y=0.5, width=1.0, height=0.5),
            encoder="videotoolbox",
        )
        command = recorder.build_command()
        self.assertIn("h264_videotoolbox", command)
        self.assertNotIn("libx264", command)

    def test_unknown_encoder_falls_back_to_software(self) -> None:
        recorder = self.build_recorder(
            CropConfig(x=0.0, y=0.5, width=1.0, height=0.5),
            encoder="not-a-real-encoder",
        )
        command = recorder.build_command()
        self.assertIn("libx264", command)


if __name__ == "__main__":
    unittest.main()
