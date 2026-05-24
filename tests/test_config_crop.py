from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from detector.config import CropConfig, load_config
from detector.frame_processing import crop_frame, prepare_frame, resolve_crop


class ConfigCropTests(unittest.TestCase):
    def test_explicit_bottom_half_crop_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "detector.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    cameras:
                      - name: front
                        display_name: Front
                        detect_rtsp_url: rtsp://example/front
                        record_rtsp_url: rtsp://example/front
                        crop:
                          x: 0.0
                          y: 0.5
                          width: 1.0
                          height: 0.5
                      - name: back
                        display_name: Back
                        detect_rtsp_url: rtsp://example/back
                        record_rtsp_url: rtsp://example/back
                    """
                ).strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config.cameras[0].crop, CropConfig(x=0.0, y=0.5, width=1.0, height=0.5))
        self.assertTrue(config.cameras[0].crop.as_dict()["active"])
        self.assertTrue(config.cameras[1].crop.is_full_frame())

    def test_camera_without_crop_defaults_to_full_frame(self) -> None:
        """No implicit per-name magic — cameras default to full frame."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "detector.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    cameras:
                      - name: anycam
                        display_name: Anycam
                        detect_rtsp_url: rtsp://example/anycam
                        record_rtsp_url: rtsp://example/anycam
                    """
                ).strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertTrue(config.cameras[0].crop.is_full_frame())

    def test_invalid_crop_outside_frame_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "detector.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    cameras:
                      - name: front
                        display_name: Front
                        detect_rtsp_url: rtsp://example/front
                        record_rtsp_url: rtsp://example/front
                        crop:
                          x: 0.2
                          y: 0.7
                          width: 0.9
                          height: 0.4
                    """
                ).strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "stay within the source frame"):
                load_config(config_path)

    def test_crop_and_prepare_frame_keep_bottom_half_visible(self) -> None:
        import numpy as np

        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[360:, :] = 255
        crop = CropConfig(x=0.0, y=0.5, width=1.0, height=0.5)

        rect = resolve_crop(frame, crop)
        self.assertEqual((rect.x, rect.y, rect.width, rect.height), (0, 360, 1280, 360))

        cropped = crop_frame(frame, crop)
        self.assertEqual(cropped.shape[:2], (360, 1280))
        self.assertEqual(int(cropped.mean()), 255)

        prepared = prepare_frame(frame, crop=crop, target_size=(640, 640))
        self.assertEqual(prepared.shape[:2], (180, 640))
        self.assertEqual(int(prepared.mean()), 255)

    def test_encoder_default_and_override(self) -> None:
        """RecordingConfig.encoder defaults to 'software' and respects YAML override."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "detector.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    cameras:
                      - name: front
                        display_name: Front
                        detect_rtsp_url: rtsp://example/front
                        record_rtsp_url: rtsp://example/front
                    """
                ).strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config.recording.encoder, "software")

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "detector.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    recording:
                      encoder: vaapi
                      vaapi_device: /dev/dri/renderD129
                    cameras:
                      - name: front
                        display_name: Front
                        detect_rtsp_url: rtsp://example/front
                        record_rtsp_url: rtsp://example/front
                    """
                ).strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config.recording.encoder, "vaapi")
        self.assertEqual(config.recording.vaapi_device, "/dev/dri/renderD129")


if __name__ == "__main__":
    unittest.main()
