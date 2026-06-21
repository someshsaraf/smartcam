"""Tests for Hailo NMS output parsing (no hardware required)."""

import numpy as np

from app.hailo_yolov8_backend import HailoYolov8Detector


def test_parse_ragged_batch_1_by_80():
    det = HailoYolov8Detector.__new__(HailoYolov8Detector)
    det._person_conf = 0.25
    det._animal_conf = 0.20
    det._output_name = "out"

    person_box = np.array([[10.0, 20.0, 200.0, 300.0, 0.9]], dtype=np.float32)
    empty = np.zeros((0, 5), dtype=np.float32)
    groups = np.empty(80, dtype=object)
    for i in range(80):
        groups[i] = person_box if i == 0 else empty
    raw = {"out": np.array([groups], dtype=object)}

    out = det._parse_nms_output(raw, 1920, 1080, 1.0, 0.0, 0.0)
    assert len(out) == 1
    assert out[0]["label"] == "person"
    assert out[0]["category"] == "person"


def test_parse_normalized_zero_one_model_coords():
    from app.hailo_yolov8_backend import _box_to_normalized

    # 0–1 on 640 canvas: person in upper-left of letterboxed input
    norm = _box_to_normalized(0.1, 0.1, 0.5, 0.4, 1920, 1080, 0.333, 0.0, 140.0, model_size=640)
    assert norm is not None
    assert norm["w"] > 0.01
    assert norm["h"] > 0.01


def test_parse_already_frame_normalized():
    from app.hailo_yolov8_backend import _box_to_normalized

    norm = _box_to_normalized(0.2, 0.3, 0.7, 0.5, 1920, 1080, 1.0, 0.0, 0.0, model_size=640)
    assert norm is not None
    assert abs(norm["x"] - 0.3) < 0.001
    assert abs(norm["w"] - 0.2) < 0.001
