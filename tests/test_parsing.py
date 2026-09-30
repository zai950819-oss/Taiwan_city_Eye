from godeye.geo import parse_wkt_lines
from godeye.tdx import _congestion, normalize_cctv


def test_wkt_linestring():
    assert parse_wkt_lines("LINESTRING(121.5 25.0, 121.6 25.1)") == [[[121.5, 25.0], [121.6, 25.1]]]


def test_wkt_multilinestring():
    lines = parse_wkt_lines("MULTILINESTRING((1 2, 3 4), (5 6, 7 8))")
    assert len(lines) == 2 and lines[1][1] == [7.0, 8.0]


def test_congestion_levels():
    assert [_congestion(v) for v in ("1", "3", "-99", None, "2,3")] == [1, 3, 0, 0, 2]


def test_normalize_cctv():
    raw = {"CCTVID": "CCTV-N1-N-10", "PositionLon": 121.5, "PositionLat": 25.0,
           "VideoStreamURL": "https://cctv.example/x", "RoadName": "國道1號", "LocationMile": "10K"}
    c = normalize_cctv(raw, "Freeway")
    assert c["id"] == "Freeway:CCTV-N1-N-10" and c["name"] == "國道1號 10K"
    assert normalize_cctv({**raw, "VideoStreamURL": ""}, "Freeway") is None


def test_player_classification():
    from godeye.player import classify_url
    assert classify_url("https://cctvn.freeway.gov.tw/abs2mjpg/bmjpg?camera=10000") == "mjpeg"
    assert classify_url("https://hls.bote.gov.taipei/live/index.html?id=001") == "iframe"
    assert classify_url("https://atis.ntpc.gov.tw/ATIS/ShowFrame4CCTV/C000002") == "iframe"
    assert classify_url("https://x.gov.tw/a/playlist.m3u8") == "hls"
    assert classify_url("https://cctv-ss02.thb.gov.tw:443/T62-9K+020") is None  # probed at runtime
