"""Motion detection using MOG2 background subtraction."""

import cv2
import numpy as np


class MotionDetector:
    """
    Motion detection using MOG2 background subtraction.

    Why MOG2 over frame differencing:
    1. Adapts to gradual lighting changes
    2. Better handling of shadows
    3. More robust to camera noise
    4. Built-in learning rate for dynamic environments
    """

    def __init__(
        self,
        threshold: int = 25,
        min_area: int = 5000,
        blur_size: int = 21,
        history: int = 500,
        detect_shadows: bool = True,
    ):
        self.threshold = threshold
        self.min_area = min_area
        self.blur_size = blur_size

        # MOG2 background subtractor
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=history, varThreshold=threshold, detectShadows=detect_shadows
        )

        # Morphological kernels for noise removal
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    def detect_motion(
        self, frame: np.ndarray
    ) -> tuple[bool, list[tuple[int, int, int, int]], np.ndarray]:
        """
        Detect motion in frame.

        Returns:
            - motion_detected: bool
            - regions: List of (x, y, w, h) bounding boxes with motion
            - mask: Motion mask for visualization/debugging
        """
        # Apply Gaussian blur to reduce noise
        blurred = cv2.GaussianBlur(frame, (self.blur_size, self.blur_size), 0)

        # Apply background subtraction
        fg_mask = self.bg_subtractor.apply(blurred)

        # Remove shadows (gray pixels become black)
        _, fg_mask = cv2.threshold(fg_mask, 250, 255, cv2.THRESH_BINARY)

        # Morphological operations to remove noise
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self._kernel)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, self._kernel)
        fg_mask = cv2.dilate(fg_mask, self._kernel, iterations=2)

        # Find contours
        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Filter by area and get bounding boxes
        regions = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area >= self.min_area:
                x, y, w, h = cv2.boundingRect(contour)
                regions.append((x, y, w, h))

        motion_detected = len(regions) > 0
        return motion_detected, regions, fg_mask

    def reset(self):
        """Reset the background model (e.g., after camera restart)."""
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=self.threshold, detectShadows=True
        )
