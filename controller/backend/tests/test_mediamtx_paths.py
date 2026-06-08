from app.mediamtx_paths import rtsp_url_has_userinfo


def test_rtsp_url_has_userinfo():
    assert not rtsp_url_has_userinfo("")
    assert not rtsp_url_has_userinfo("http://x")
    assert not rtsp_url_has_userinfo("rtsp://192.168.1.10:554/stream1")
    assert rtsp_url_has_userinfo("rtsp://admin:secret@192.168.1.10:554/stream1")
    assert rtsp_url_has_userinfo("rtsps://u:p@host/path")
