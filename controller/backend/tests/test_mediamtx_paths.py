from app.mediamtx_paths import redact_rtsp_url_for_debug, rtsp_url_has_userinfo


def test_redact_rtsp_url_for_debug():
    assert redact_rtsp_url_for_debug("") == ""
    assert redact_rtsp_url_for_debug("rtsp://192.168.1.10:554/stream1") == "rtsp://192.168.1.10:554/stream1"
    assert (
        redact_rtsp_url_for_debug("rtsp://admin:secret@192.168.1.10:554/stream1")
        == "rtsp://admin:***@192.168.1.10:554/stream1"
    )
    assert (
        redact_rtsp_url_for_debug("rtsps://u:p@host/path")
        == "rtsps://u:***@host/path"
    )


def test_rtsp_url_has_userinfo():
    assert not rtsp_url_has_userinfo("")
    assert not rtsp_url_has_userinfo("http://x")
    assert not rtsp_url_has_userinfo("rtsp://192.168.1.10:554/stream1")
    assert rtsp_url_has_userinfo("rtsp://admin:secret@192.168.1.10:554/stream1")
    assert rtsp_url_has_userinfo("rtsps://u:p@host/path")
