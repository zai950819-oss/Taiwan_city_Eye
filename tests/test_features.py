import time

from godeye import mock
from godeye.analytics import sync_alerts
from godeye.cwa import parse_stations
from godeye.db import DB
from godeye.vision import level_from, moving_ratio


def test_moving_ratio_queue_vs_flow():
    f1 = [(2, 0, 0, 10, 10), (2, 20, 0, 30, 10), (7, 40, 0, 60, 20)]
    same = list(f1)
    moved = [(2, 5, 0, 15, 10), (2, 26, 0, 36, 10), (7, 52, 0, 72, 20)]
    assert moving_ratio(f1, same) == (0.0, 3)
    assert moving_ratio(f1, moved)[0] == 1.0
    assert moving_ratio([], moved) == (None, 0)


def test_level_uses_motion_first():
    assert level_from({"total": 20, "moving": 0.1}) == 3
    assert level_from({"total": 20, "moving": 0.9}) == 1
    assert level_from({"total": 30, "moving": None}) == 3


def test_cwa_parse_handles_sentinels():
    data = {"records": {"Station": [{
        "StationId": "C0A560", "StationName": "福山",
        "GeoInfo": {"Coordinates": [{"CoordinateName": "TWD67", "StationLatitude": 0, "StationLongitude": 0},
                                    {"CoordinateName": "WGS84", "StationLatitude": 24.9, "StationLongitude": 121.5}]},
        "RainfallElement": {"Now": {"Precipitation": 1.5}, "Past1hr": {"Precipitation": -998.0}},
    }]}}
    st = parse_stations(data)
    assert st[0]["lat"] == 24.9 and st[0]["now"] == 1.5 and st[0]["past1h"] is None


def test_slowdown_alert_opens_and_resolves(tmp_path):
    db = DB(tmp_path / "t.db")
    shape = {"id": "S1", "name": "國道1號 test", "lines": [[[121.50, 25.00], [121.51, 25.01]]]}
    db.upsert_sections([shape])
    now = time.time()
    for i in range(10):  # history: usually 95 km/h at this hour
        db.add_live([{"section_id": "S1", "speed": 95, "level": 1}], now - 3 * 3600 - i * 60 + 3 * 3600 - 2400)
    db.add_live([{"section_id": "S1", "speed": 30, "level": 3}], now)
    bbox = (121.4, 24.9, 121.6, 25.1)
    assert sync_alerts(db, bbox, now)["opened"] == 1
    a = db.alerts_in_bbox(bbox, now, active_only=True)[0]
    assert a["kind"] == "slowdown" and "slower than usual" in a["message"]
    db.add_live([{"section_id": "S1", "speed": 92, "level": 1}], now + 60)
    assert sync_alerts(db, bbox, now + 60)["resolved"] == 1


def test_mock_incident_raises_pressure():
    inc = mock.incident(time.time() + 1)
    assert inc is not None
    assert mock._pressure(inc[0], inc[1], time.time() + 1) > 0.8
