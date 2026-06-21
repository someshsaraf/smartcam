"""Tests for ByteTracker consecutive-frame confirmation."""

from app.byte_tracker import ByteTracker


def _person(x: float, y: float) -> dict:
    return {
        "x": x,
        "y": y,
        "w": 0.1,
        "h": 0.2,
        "label": "person",
        "category": "person",
        "score": 0.9,
    }


def test_track_requires_two_frames_for_confirm():
    tracker = ByteTracker()
    d1 = _person(0.1, 0.1)
    r1 = tracker.update([d1], confirm_frames=2)
    assert len(r1) == 1
    assert r1[0]["track_id"] == 1
    assert r1[0]["confirmed"] is False

    r2 = tracker.update([d1], confirm_frames=2)
    assert len(r2) == 1
    assert r2[0]["confirmed"] is True


def test_track_lost_resets_confirmation():
    tracker = ByteTracker(max_missed=1)
    d1 = _person(0.1, 0.1)
    tracker.update([d1], confirm_frames=2)
    confirmed = tracker.update([d1], confirm_frames=2)
    assert confirmed[0]["confirmed"] is True
    tracker.update([], confirm_frames=2)
    fresh = tracker.update([_person(0.5, 0.5)], confirm_frames=2)
    assert fresh[0]["confirmed"] is False
