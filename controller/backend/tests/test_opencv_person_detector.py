"""Heuristic tests for person/animal post-processing (no SSD weights required)."""

from __future__ import annotations

import sys
import types

import pytest

cv2 = types.ModuleType("cv2")
cv2.dnn = types.SimpleNamespace(readNetFromCaffe=lambda *a, **k: None)
sys.modules.setdefault("cv2", cv2)
sys.modules.setdefault("numpy", types.ModuleType("numpy"))

from app.opencv_person_detector import (  # noqa: E402
    _is_likely_person_false_positive,
    _is_likely_pet_person_mislabel,
    _refine_person_and_animal_boxes,
)


def test_dog_mislabeled_as_person_reclassifies_to_dog():
    dog = {
        "category": "person",
        "label": "person",
        "score": 0.473,
        "x": 0.15,
        "y": 0.35,
        "w": 0.38,
        "h": 0.32,
    }
    assert _is_likely_pet_person_mislabel(dog)
    out = _refine_person_and_animal_boxes([dog], [], [])
    assert len(out) == 1
    assert out[0]["category"] == "animal"
    assert out[0]["label"] == "dog"


def test_tall_narrow_shadow_person_suppressed():
    fp = {
        "category": "person",
        "label": "person",
        "score": 0.749,
        "x": 0.28,
        "y": 0.08,
        "w": 0.12,
        "h": 0.55,
    }
    assert _is_likely_person_false_positive(fp)
    assert _refine_person_and_animal_boxes([fp], [], []) == []


def test_confident_standing_person_kept():
    human = {
        "category": "person",
        "label": "person",
        "score": 0.82,
        "x": 0.4,
        "y": 0.1,
        "w": 0.18,
        "h": 0.55,
    }
    assert not _is_likely_person_false_positive(human)
    assert not _is_likely_pet_person_mislabel(human)
    out = _refine_person_and_animal_boxes([human], [], [])
    assert len(out) == 1
    assert out[0]["category"] == "person"


def test_human_profile_mislabeled_as_dog_reclassifies_to_person():
    # Large profile box starting near top of frame (eye-level camera).
    profile = {
        "category": "animal",
        "label": "dog",
        "score": 0.967,
        "x": 0.0,
        "y": 0.05,
        "w": 0.58,
        "h": 0.72,
    }
    out = _refine_person_and_animal_boxes([], [profile], [])
    assert len(out) == 1
    assert out[0]["category"] == "person"
    assert out[0]["label"] == "person"


def test_overlapping_person_wins_over_dog():
    dog = {
        "category": "animal",
        "label": "dog",
        "score": 0.967,
        "x": 0.0,
        "y": 0.05,
        "w": 0.58,
        "h": 0.72,
    }
    person = {
        "category": "person",
        "label": "person",
        "score": 0.88,
        "x": 0.02,
        "y": 0.08,
        "w": 0.52,
        "h": 0.65,
    }
    out = _refine_person_and_animal_boxes([person], [dog], [])
    assert len(out) == 1
    assert out[0]["category"] == "person"


def test_human_head_mislabeled_as_bird_reclassifies_to_person():
    bird = {
        "category": "animal",
        "label": "bird",
        "score": 0.78,
        "x": 0.02,
        "y": 0.12,
        "w": 0.28,
        "h": 0.38,
    }
    out = _refine_person_and_animal_boxes([], [bird], [])
    assert len(out) == 1
    assert out[0]["category"] == "person"
    assert out[0]["label"] == "person"
