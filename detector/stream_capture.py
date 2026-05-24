"""Threaded RTSP stream capture with automatic reconnection."""

import threading
import time
from queue import Queue

import cv2
import numpy as np

from .frame_processing import prepare_frame


class RTSPCapture:
    """
    Threaded RTSP stream capture with automatic reconnection.

    Key design decisions:
    1. Dedicated capture thread to prevent frame drops
    2. Frame queue with max size to prevent memory buildup
    3. Auto-reconnect on stream failure
    4. Frame downscaling in capture thread for efficiency
    """

    def __init__(
        self,
        rtsp_url: str,
        target_fps: float = 5.0,
        frame_size: tuple[int, int] = (640, 640),
        crop=None,
    ):
        self.rtsp_url = rtsp_url
        self.target_fps = target_fps
        self.frame_size = frame_size
        self.crop = crop
        self.frame_interval = 1.0 / target_fps

        self._frame_queue: Queue = Queue(maxsize=3)
        self._stop_event = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._cap: cv2.VideoCapture | None = None
        self._connected = False
        self._last_frame_size: tuple[int, int] | None = None

    def start(self):
        """Start the capture thread."""
        self._stop_event.clear()
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()

    def stop(self):
        """Stop the capture thread."""
        self._stop_event.set()
        if self._capture_thread:
            self._capture_thread.join(timeout=5.0)
        if self._cap:
            self._cap.release()
            self._cap = None

    def is_connected(self) -> bool:
        """Check if stream is connected."""
        return self._connected

    def get_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        """Get the most recent frame, discarding older ones."""
        frame = None
        try:
            # Drain queue to get most recent frame
            while not self._frame_queue.empty():
                frame = self._frame_queue.get_nowait()
        except Exception:
            pass
        return frame

    def last_frame_size(self) -> tuple[int, int] | None:
        return self._last_frame_size

    def _capture_loop(self):
        """Main capture loop running in dedicated thread."""
        reconnect_delay = 1.0

        while not self._stop_event.is_set():
            if self._cap is None or not self._cap.isOpened():
                self._connected = False
                if not self._connect():
                    time.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 30.0)
                    continue
                reconnect_delay = 1.0

            ret, frame = self._cap.read()
            if not ret:
                self._connected = False
                self._cap.release()
                self._cap = None
                time.sleep(1.0)
                continue

            self._connected = True

            # Crop before resize so all downstream processing sees the same image.
            frame = prepare_frame(frame, crop=self.crop, target_size=self.frame_size)
            self._last_frame_size = (frame.shape[1], frame.shape[0])

            # Put frame in queue (drop old frames if full)
            if self._frame_queue.full():
                try:
                    self._frame_queue.get_nowait()
                except Exception:
                    pass
            self._frame_queue.put(frame)

            time.sleep(self.frame_interval)

    def _connect(self) -> bool:
        """Connect to RTSP stream with optimized settings."""
        try:
            self._cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return self._cap.isOpened()
        except Exception:
            return False
