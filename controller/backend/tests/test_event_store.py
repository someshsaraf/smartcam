import os
import tempfile

import pytest

from app import event_store


@pytest.fixture
def isolated_events(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)
    monkeypatch.setenv("SMARTCAM_EVENTS_JSON", path)
    event_store._loaded = False
    event_store._events = []
    event_store._next_id = 1
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def test_add_and_list_events(isolated_events):
    row = event_store.add_event(0, "person_detected", 1_700_000_000.0, recording_id="evt_1")
    assert row["event_type"] == "person_detected"
    assert row["camera_id"] == 0
    assert row["recording_id"] == "evt_1"

    listed = event_store.list_events(0)
    assert len(listed) == 1
    assert listed[0]["id"] == row["id"]


def test_update_event_filename(isolated_events):
    event_store.add_event(1, "person_detected", 1_700_000_100.0, recording_id="evt_abc")
    ok = event_store.update_event_by_recording_id(1, "evt_abc", filename="evt_20260101_120000.mp4")
    assert ok is True
    listed = event_store.list_events(1)
    assert listed[0]["filename"] == "evt_20260101_120000.mp4"


def test_delete_filtered(isolated_events):
    event_store.add_event(2, "person_detected", 1_700_000_000.0)
    event_store.add_event(2, "person_detected", 1_800_000_000.0)
    removed = event_store.delete_events_filtered(
        2,
        from_ts="2026-01-01T00:00:00+00:00",
        to_ts="2026-12-31T23:59:59+00:00",
    )
    assert removed == 1
    assert len(event_store.list_events(2)) == 1
