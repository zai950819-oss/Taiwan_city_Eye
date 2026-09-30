"""Zero-shot road-condition tagging with CLIP (optional).

Labels a frame as dry / wet / flood / fog / smoke / night. No training data needed —
CLIP compares the image against text prompts. Enabled when `transformers` is installed.
"""
from __future__ import annotations

import io
import logging
import threading

log = logging.getLogger("godeye.conditions")

PROMPTS = {
    "dry": "a traffic camera photo of a dry road",
    "wet": "a traffic camera photo of a wet road in the rain",
    "flood": "a traffic camera photo of a flooded road with standing water",
    "fog": "a traffic camera photo of a road in thick fog with low visibility",
    "smoke": "a traffic camera photo of a road with smoke or fire",
    "night": "a traffic camera photo of a dark road at night",
}
MODEL = "openai/clip-vit-base-patch32"


class ConditionTagger:
    def __init__(self):
        self._pipe = None
        self._lock = threading.Lock()
        try:
            import PIL  # noqa: F401
            import transformers  # noqa: F401
            self.available = True
        except ImportError:
            self.available = False
            log.warning("transformers not installed → road-condition tagging disabled")

    def _load(self):
        if self._pipe is None:
            from transformers import pipeline
            self._pipe = pipeline("zero-shot-image-classification", model=MODEL)
            log.info("CLIP loaded: %s", MODEL)
        return self._pipe

    def tag(self, jpeg: bytes) -> tuple[str, float] | None:
        """Blocking — call via asyncio.to_thread. Returns (label, confidence)."""
        from PIL import Image

        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        with self._lock:
            res = self._load()(img, candidate_labels=list(PROMPTS.values()))
        del img
        best = res[0]
        label = next(k for k, v in PROMPTS.items() if v == best["label"])
        return label, round(float(best["score"]), 3)
