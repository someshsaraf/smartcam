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


def test_parse_fixed_tensor():
    det = HailoYolov8Detector.__new__(HailoYolov8Detector)
    det._person_conf = 0.25
    det._animal_conf = 0.20
    det._output_name = "out"

    arr = np.zeros((1, 80, 4, 5), dtype=np.float32)
    arr[0, 0, 0] = [10.0, 20.0, 200.0, 300.0, 0.85]
    raw = {"out": arr}

    out = det._parse_nms_output(raw, 640, 480, 1.0, 0.0, 0.0)
    assert len(out) == 1
    assert out[0]["score"] == 0.85
