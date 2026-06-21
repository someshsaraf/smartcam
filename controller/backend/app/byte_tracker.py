"""
Lightweight BYTETracker for SmartCam — greedy IoU association, no extra deps.

One ``ByteTracker`` per camera thread. Detections use normalized x,y,w,h boxes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _iou(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ax1, ay1 = float(a["x"]), float(a["y"])
    ax2, ay2 = ax1 + float(a["w"]), ay1 + float(a["h"])
    bx1, by1 = float(b["x"]), float(b["y"])
    bx2, by2 = bx1 + float(b["w"]), by1 + float(b["h"])
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


class _Track:
    __slots__ = ("track_id", "bbox", "label", "category", "score", "hits", "missed")

    def __init__(
        self,
        track_id: int,
        det: Dict[str, Any],
    ) -> None:
        self.track_id = track_id
        self.bbox = {
            "x": float(det["x"]),
            "y": float(det["y"]),
            "w": float(det["w"]),
            "h": float(det["h"]),
        }
        self.label = str(det.get("label") or "")
        self.category = str(det.get("category") or "")
        self.score = float(det.get("score") or 0.0)
        self.hits = 1
        self.missed = 0

    def update(self, det: Dict[str, Any]) -> None:
        self.bbox = {
            "x": float(det["x"]),
            "y": float(det["y"]),
            "w": float(det["w"]),
            "h": float(det["h"]),
        }
        self.label = str(det.get("label") or self.label)
        self.category = str(det.get("category") or self.category)
        self.score = float(det.get("score") or self.score)
        self.hits += 1
        self.missed = 0

    def mark_missed(self) -> None:
        self.missed += 1

    def to_detection(self, *, confirmed: bool) -> Dict[str, Any]:
        return {
            **self.bbox,
            "label": self.label,
            "category": self.category,
            "score": self.score,
            "track_id": self.track_id,
            "confirmed": confirmed,
        }


class ByteTracker:
    """Greedy BYTE-style tracker with high-confidence matching only."""

    def __init__(
        self,
        *,
        iou_threshold: float = 0.3,
        max_missed: int = 30,
    ) -> None:
        if iou_threshold <= 0.0 or iou_threshold > 1.0:
            raise ValueError("iou_threshold must be in (0, 1]")
        if max_missed < 1:
            raise ValueError("max_missed must be >= 1")
        self._iou_threshold = iou_threshold
        self._max_missed = max_missed
        self._tracks: List[_Track] = []
        self._next_id = 1

    def update(
        self,
        detections: List[Dict[str, Any]],
        *,
        confirm_frames: int = 2,
    ) -> List[Dict[str, Any]]:
        if confirm_frames < 1:
            raise ValueError("confirm_frames must be >= 1")
        dets = [d for d in detections if self._valid_det(d)]
        matched_track_idxs: set[int] = set()
        matched_det_idxs: set[int] = set()
        pairs: List[Tuple[float, int, int]] = []
        for ti, track in enumerate(self._tracks):
            for di, det in enumerate(dets):
                iou = _iou(track.bbox, det)
                if iou >= self._iou_threshold:
                    pairs.append((iou, ti, di))
        pairs.sort(key=lambda t: t[0], reverse=True)
        for _, ti, di in pairs:
            if ti in matched_track_idxs or di in matched_det_idxs:
                continue
            self._tracks[ti].update(dets[di])
            matched_track_idxs.add(ti)
            matched_det_idxs.add(di)
        for ti, track in enumerate(self._tracks):
            if ti not in matched_track_idxs:
                track.mark_missed()
        for di, det in enumerate(dets):
            if di not in matched_det_idxs:
                self._tracks.append(_Track(self._next_id, det))
                self._next_id += 1
        self._tracks = [t for t in self._tracks if t.missed <= self._max_missed]
        out: List[Dict[str, Any]] = []
        for track in self._tracks:
            if track.missed > 0:
                continue
            confirmed = track.hits >= confirm_frames
            out.append(track.to_detection(confirmed=confirmed))
        return out

    @staticmethod
    def _valid_det(det: Dict[str, Any]) -> bool:
        try:
            x = float(det["x"])
            y = float(det["y"])
            w = float(det["w"])
            h = float(det["h"])
        except (KeyError, TypeError, ValueError):
            return False
        return 0.0 <= x < 1.0 and 0.0 <= y < 1.0 and w > 0.0 and h > 0.0
