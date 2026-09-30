"""Vehicle counting + motion analysis with YOLO. Counts objects — never identifies anyone.

Two frames ~2 s apart let us tell a *queue* (many vehicles, none moving) from
*heavy but flowing* traffic, which a single-frame count cannot.
Frames exist only in memory for the duration of one call.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("godeye.vision")

VEHICLE_CLASSES = {2: "cars", 3: "motorcycles", 5: "buses", 7: "trucks"}  # COCO ids
STATIONARY_IOU = 0.75


def level_from(c: dict) -> int:
    """Congestion level 1-3. Uses motion when available, density otherwise."""
    total, moving = c["total"], c.get("moving")
    if moving is not None and total >= 6:
        if moving < 0.3:
            return 3
        if moving < 0.6:
            return 2
        return 2 if total >= 25 else 1
    return 1 if total < 12 else 2 if total < 25 else 3


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def moving_ratio(boxes1: list, boxes2: list) -> tuple[float | None, int]:
    """Share of frame-1 vehicles that moved by frame 2, plus the stationary count.
    boxes: [(cls, x1, y1, x2, y2), ...]"""
    if not boxes1:
        return None, 0
    stationary = 0
    for c1, *b1 in boxes1:
        best = max((_iou(b1, b2) for c2, *b2 in boxes2 if c2 == c1), default=0.0)
        if best >= STATIONARY_IOU:
            stationary += 1
    return round(1 - stationary / len(boxes1), 3), stationary


class VehicleCounter:
    def __init__(self, weights: str = "yolov8n.pt"):
        self._weights = weights
        self._model = None
        self._lock = threading.Lock()
        try:
            import cv2  # noqa: F401
            import numpy  # noqa: F401
            from ultralytics import YOLO  # noqa: F401
            self.available = True
        except ImportError:
            self.available = False
            log.warning("ultralytics not installed → vision disabled (pip install -r requirements-vision.txt)")

    def _load(self):
        if self._model is None:
            from ultralytics import YOLO
            self._model = YOLO(self._weights)
            log.info("YOLO model loaded: %s", self._weights)
        return self._model

    def _detect(self, jpeg: bytes) -> list | None:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        with self._lock:
            res = self._load()(img, imgsz=640, conf=0.3, classes=list(VEHICLE_CLASSES), verbose=False)[0]
        boxes = [(int(c), *map(float, xyxy)) for c, xyxy in zip(res.boxes.cls.tolist(), res.boxes.xyxy.tolist())]
        del img, res
        return boxes

    def analyze(self, frames: list[bytes]) -> dict | None:
        """Blocking — call via asyncio.to_thread."""
        b1 = self._detect(frames[0])
        if b1 is None:
            return None
        counts = {name: 0 for name in VEHICLE_CLASSES.values()}
        for cls, *_ in b1:
            counts[VEHICLE_CLASSES[cls]] += 1
        counts["total"] = len(b1)
        counts["moving"], counts["stationary"] = None, 0
        if len(frames) > 1:
            b2 = self._detect(frames[1])
            if b2 is not None:
                counts["moving"], counts["stationary"] = moving_ratio(b1, b2)
        return counts
