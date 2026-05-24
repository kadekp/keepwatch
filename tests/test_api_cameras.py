from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from detector.api import create_app
from detector.config import load_config


def _write_config(temp_dir: Path) -> Path:
    config_path = temp_dir / "detector.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            cameras:
              - name: front
                display_name: Front Door
                detect_rtsp_url: rtsp://example/front
                record_rtsp_url: rtsp://example/front
              - name: back
                display_name: Back Yard
                detect_rtsp_url: rtsp://example/back
                record_rtsp_url: rtsp://example/back
                crop:
                  x: 0.0
                  y: 0.5
                  width: 1.0
                  height: 0.5
            """
        ).strip(),
        encoding="utf-8",
    )
    return config_path


class ApiCamerasEndpointTests(unittest.TestCase):
    def test_endpoint_returns_configured_cameras(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            config = load_config(_write_config(temp_dir))

            app = create_app(config)
            # Do not enter the FastAPI lifespan; the camera endpoint reads
            # config directly and does not need the DetectorSupervisor to run.
            client = TestClient(app)

            response = client.get("/api/cameras")
            self.assertEqual(response.status_code, 200)

            payload = response.json()
            self.assertEqual(payload["count"], 2)

            names = [item["name"] for item in payload["items"]]
            self.assertEqual(names, ["front", "back"])

            front = payload["items"][0]
            self.assertEqual(front["display_name"], "Front Door")
            self.assertFalse(front["crop"]["active"])

            back = payload["items"][1]
            self.assertEqual(back["display_name"], "Back Yard")
            self.assertTrue(back["crop"]["active"])
            self.assertEqual(back["crop"]["y"], 0.5)


if __name__ == "__main__":
    unittest.main()
