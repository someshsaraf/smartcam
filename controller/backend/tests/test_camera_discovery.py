"""Unit tests for ONVIF WS-Discovery XML parsing (no network)."""

from app.camera_discovery import _parse_probe_match_xaddrs, _scopes_friendly_name


def test_parse_probe_match_xaddrs_basic():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
<s:Body>
<d:ProbeMatches>
<d:ProbeMatch>
<d:XAddrs>http://192.168.1.50/onvif/device_service</d:XAddrs>
<d:Scopes>onvif://www.onvif.org/name/MyCam</d:Scopes>
</d:ProbeMatch>
</d:ProbeMatches>
</s:Body>
</s:Envelope>"""
    xaddrs, scopes = _parse_probe_match_xaddrs(xml)
    assert xaddrs == ["http://192.168.1.50/onvif/device_service"]
    assert scopes and "onvif" in scopes


def test_scopes_friendly_name():
    s = "onvif://www.onvif.org/name/Front%20Door"
    assert "Front" in _scopes_friendly_name(s)
