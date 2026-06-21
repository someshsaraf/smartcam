"""Tests for detection pipeline env helpers."""

import os

from app.detection_pipeline import _event_confirm_frames, pipeline_diagnostics


def test_event_confirm_frames_default(monkeypatch):
    monkeypatch.delenv("SMARTCAM_EVENT_CONFIRM_FRAMES", raising=False)
    monkeypatch.delenv("SMARTCAM_PERSON_TRIGGER_MIN_FRAMES", raising=False)
    assert _event_confirm_frames() == 2


def test_event_confirm_frames_env(monkeypatch):
    monkeypatch.setenv("SMARTCAM_EVENT_CONFIRM_FRAMES", "3")
    assert _event_confirm_frames() == 3


def test_pipeline_diagnostics_shape():
    diag = pipeline_diagnostics()
    assert "pipeline" in diag
    assert "backend" in diag
    assert "event_confirm_frames" in diag
