"""YOLOv8n person detection with Apple Silicon MPS acceleration."""

from dataclasses import dataclass

import numpy as np


@dataclass
class PersonDetection:
    """Represents a detected person."""

    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2
    confidence: float
    class_id: int = 0  # 0 = person in COCO


class PersonDetector:
    """
    YOLOv8n person detection with Apple Silicon MPS acceleration.

    Design decisions:
    1. Use YOLOv8n (nano) for speed/efficiency balance
    2. MPS backend for Apple Silicon GPU acceleration
    3. Filter to person class only (class_id=0)
    4. Configurable confidence threshold
    """

    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        confidence_threshold: float = 0.5,
        use_mps: bool = True,
    ):
        self.confidence_threshold = confidence_threshold

        # Import here to allow graceful failure if not installed
        from ultralytics import YOLO

        # Load model
        self.model = YOLO(model_name)

        # Set device (MPS for Apple Silicon, else CPU)
        self.device = "cpu"
        if use_mps:
            try:
                import torch

                if torch.backends.mps.is_available():
                    self.device = "mps"
            except ImportError:
                pass

    def detect(self, frame: np.ndarray) -> list[PersonDetection]:
        """
        Detect persons in frame.

        Args:
            frame: BGR image as numpy array

        Returns:
            List of PersonDetection objects
        """
        # Run inference
        results = self.model(
            frame,
            device=self.device,
            conf=self.confidence_threshold,
            classes=[0],  # Person class only
            verbose=False,
        )

        detections = []
        for result in results:
            if result.boxes is None:
                continue

            for box in result.boxes:
                # Get bounding box coordinates
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                confidence = float(box.conf[0])
                class_id = int(box.cls[0])

                detections.append(
                    PersonDetection(
                        bbox=(int(x1), int(y1), int(x2), int(y2)),
                        confidence=confidence,
                        class_id=class_id,
                    )
                )

        return detections
