"""SQLite store. Aggregates only — no frames, faces or plates are ever written."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
  id TEXT PRIMARY KEY, source TEXT, name TEXT, road TEXT, direction TEXT,
  lon REAL, lat REAL, stream_url TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS vision (
  camera_id TEXT, ts REAL, cars INT, motorcycles INT, buses INT, trucks INT,
  total INT, level INT);
CREATE INDEX IF NOT EXISTS vision_cam_ts ON vision(camera_id, ts);
CREATE INDEX IF NOT EXISTS vision_ts ON vision(ts);
CREATE TABLE IF NOT EXISTS sections (
  id TEXT PRIMARY KEY, name TEXT, road TEXT, direction TEXT, lines TEXT);
CREATE TABLE IF NOT EXISTS section_live (section_id TEXT, ts REAL, speed REAL, level INT);
CREATE INDEX IF NOT EXISTS live_sec_ts ON section_live(section_id, ts);
CREATE INDEX IF NOT EXISTS live_ts ON section_live(ts);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, kind TEXT, entity_type TEXT, entity_id TEXT,
  name TEXT, lon REAL, lat REAL, severity INT, message TEXT,
  started REAL, updated REAL, resolved REAL);
CREATE INDEX IF NOT EXISTS alerts_key ON alerts(key, resolved);
CREATE INDEX IF NOT EXISTS alerts_time ON alerts(started);
CREATE TABLE IF NOT EXISTS rain (
  station_id TEXT PRIMARY KEY, name TEXT, lon REAL, lat REAL, now REAL, past1h REAL, ts REAL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

# columns added after the first release → migrated in place
VISION_EXTRA = {"moving": "REAL", "condition": "TEXT", "cond_conf": "REAL"}


def _in(ids) -> str:
    return ",".join("?" * len(ids))


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(path, check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._c.execute("PRAGMA journal_mode=WAL")
            self._c.executescript(SCHEMA)
            have = {r["name"] for r in self._c.execute("PRAGMA table_info(vision)")}
            for col, typ in VISION_EXTRA.items():
                if col not in have:
                    self._c.execute(f"ALTER TABLE vision ADD COLUMN {col} {typ}")
            self._c.commit()

    def _q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self._c.execute(sql, args).fetchall()

    def _many(self, sql: str, rows: list) -> None:
        with self._lock, self._c:
            self._c.executemany(sql, rows)

    def _exec(self, sql: str, args=()) -> int:
        with self._lock, self._c:
            return self._c.execute(sql, args).lastrowid

    # ---------------- writes ----------------
    def upsert_cameras(self, cams: list[dict]) -> None:
        now = time.time()
        self._many(
            "INSERT INTO cameras VALUES (:id,:source,:name,:road,:direction,:lon,:lat,:stream_url,:ts) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, lon=excluded.lon, lat=excluded.lat, "
            "stream_url=excluded.stream_url, updated_at=excluded.updated_at",
            [{**c, "ts": now} for c in cams])

    def upsert_sections(self, shapes: list[dict], meta: dict[str, dict] | None = None) -> None:
        meta = meta or {}
        rows = []
        for s in shapes:
            m = meta.get(s["id"], {})
            rows.append((s["id"], m.get("name") or s.get("name", ""), m.get("road", ""),
                         m.get("direction", ""), json.dumps(s["lines"])))
        self._many("INSERT OR REPLACE INTO sections VALUES (?,?,?,?,?)", rows)

    def add_live(self, rows: list[dict], ts: float) -> None:
        self._many("INSERT INTO section_live VALUES (?,?,?,?)",
                   [(r["section_id"], ts, r["speed"], r["level"]) for r in rows])

    def add_vision(self, cam_id: str, ts: float, c: dict, level: int) -> None:
        self._many(
            "INSERT INTO vision (camera_id, ts, cars, motorcycles, buses, trucks, total, level, moving, "
            "condition, cond_conf) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(cam_id, ts, c["cars"], c["motorcycles"], c["buses"], c["trucks"], c["total"], level,
              c.get("moving"), c.get("condition"), c.get("cond_conf"))])

    def replace_rain(self, stations: list[dict]) -> None:
        with self._lock, self._c:
            self._c.execute("DELETE FROM rain")
            self._c.executemany("INSERT INTO rain VALUES (:id,:name,:lon,:lat,:now,:past1h,:ts)", stations)

    def set_meta(self, k: str, v) -> None:
        self._many("INSERT OR REPLACE INTO meta VALUES (?,?)", [(k, json.dumps(v))])

    def prune(self, hours: int) -> None:
        cutoff = time.time() - hours * 3600
        with self._lock, self._c:
            self._c.execute("DELETE FROM vision WHERE ts < ?", (cutoff,))
            self._c.execute("DELETE FROM section_live WHERE ts < ?", (cutoff,))
            self._c.execute("DELETE FROM alerts WHERE resolved IS NOT NULL AND resolved < ?", (cutoff,))

    def delete_mock(self) -> None:
        with self._lock, self._c:
            for t, col in (("cameras", "id"), ("vision", "camera_id"), ("sections", "id"),
                           ("section_live", "section_id"), ("alerts", "entity_id"), ("rain", "station_id")):
                self._c.execute(f"DELETE FROM {t} WHERE {col} LIKE 'MOCK:%'")

    # ---------------- alerts ----------------
    def active_alert(self, key: str):
        rows = self._q("SELECT * FROM alerts WHERE key=? AND resolved IS NULL", (key,))
        return rows[0] if rows else None

    def open_alert(self, a: dict, now: float) -> int:
        return self._exec(
            "INSERT INTO alerts (key, kind, entity_type, entity_id, name, lon, lat, severity, message, "
            "started, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (a["key"], a["kind"], a["entity_type"], a["entity_id"], a["name"], a["lon"], a["lat"],
             a["severity"], a["message"], now, now))

    def touch_alert(self, alert_id: int, severity: int, message: str, now: float) -> None:
        self._exec("UPDATE alerts SET severity=?, message=?, updated=? WHERE id=?",
                   (severity, message, now, alert_id))

    def resolve_alerts(self, ids: list[int], now: float) -> None:
        if ids:
            self._exec(f"UPDATE alerts SET resolved=? WHERE id IN ({_in(ids)})", (now, *ids))

    def alerts_in_bbox(self, bbox, since: float, active_only: bool = False) -> list[sqlite3.Row]:
        w, s, e, n = bbox
        sql = ("SELECT * FROM alerts WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ? "
               "AND (resolved IS NULL OR resolved > ?)")
        if active_only:
            sql += " AND resolved IS NULL"
        return self._q(sql + " ORDER BY resolved IS NOT NULL, severity DESC, started DESC LIMIT 200",
                       (w, e, s, n, since))

    # ---------------- reads ----------------
    def meta(self) -> dict:
        return {r["k"]: json.loads(r["v"]) for r in self._q("SELECT * FROM meta")}

    _CAM_JOIN = """
      SELECT c.*, v.ts AS v_ts, v.cars, v.motorcycles, v.buses, v.trucks, v.total, v.level,
             v.moving, v.condition, v.cond_conf
      FROM cameras c LEFT JOIN vision v ON v.rowid = (
        SELECT rowid FROM vision WHERE camera_id = c.id ORDER BY ts DESC LIMIT 1)"""

    def cameras(self) -> list[sqlite3.Row]:
        return self._q(self._CAM_JOIN)

    def cameras_in_bbox(self, bbox) -> list[sqlite3.Row]:
        w, s, e, n = bbox
        return self._q(self._CAM_JOIN + " WHERE c.lon BETWEEN ? AND ? AND c.lat BETWEEN ? AND ?",
                       (w, e, s, n))

    def has_live_history(self) -> bool:
        return bool(self._q("SELECT 1 FROM section_live LIMIT 1"))

    def camera(self, cam_id: str):
        rows = self._q("SELECT * FROM cameras WHERE id = ?", (cam_id,))
        return rows[0] if rows else None

    def sections_latest(self) -> list[sqlite3.Row]:
        ts = self._q("SELECT max(ts) AS t FROM section_live")[0]["t"]
        return self._q("""SELECT s.*, l.speed, l.level, l.ts FROM sections s
                          LEFT JOIN section_live l ON l.section_id = s.id AND l.ts = ?""", (ts,))

    def camera_history(self, cam_id: str, hours: float) -> list[sqlite3.Row]:
        return self._q("SELECT ts, total, level, moving FROM vision WHERE camera_id=? AND ts>? ORDER BY ts",
                       (cam_id, time.time() - hours * 3600))

    def network_history(self, hours: float, bucket_s: int = 600) -> list[sqlite3.Row]:
        return self._q(f"""SELECT CAST(ts/{bucket_s} AS INT)*{bucket_s} AS t,
                             avg(speed) AS avg_speed,
                             sum(level = 3) * 1.0 / count(DISTINCT ts) AS jams
                           FROM section_live WHERE ts > ? GROUP BY t ORDER BY t""",
                       (time.time() - hours * 3600,))

    def rain_in_bbox(self, bbox) -> list[sqlite3.Row]:
        w, s, e, n = bbox
        return self._q("SELECT * FROM rain WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?", (w, e, s, n))

    # ---------------- replay ----------------
    def replay_sections(self, ids: list[str], since: float, step: int) -> list[sqlite3.Row]:
        if not ids:
            return []
        return self._q(f"""SELECT section_id AS id, CAST(ts/{step} AS INT)*{step} AS t,
                             round(avg(speed), 1) AS speed, max(level) AS level
                           FROM section_live WHERE ts > ? AND section_id IN ({_in(ids)})
                           GROUP BY id, t""", (since, *ids))

    def replay_cameras(self, ids: list[str], since: float, step: int) -> list[sqlite3.Row]:
        if not ids:
            return []
        return self._q(f"""SELECT camera_id AS id, CAST(ts/{step} AS INT)*{step} AS t,
                             CAST(round(avg(total)) AS INT) AS total, max(level) AS level
                           FROM vision WHERE ts > ? AND camera_id IN ({_in(ids)})
                           GROUP BY id, t""", (since, *ids))

    # ---------------- baselines (same hour-of-day ±1h, excluding the last 30 min) ----------------
    _HOUR = "ABS(CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INT) - ?) <= 1"

    def section_baselines(self, ids: list[str], hour: int, before: float) -> dict[str, tuple[float, int]]:
        if not ids:
            return {}
        rows = self._q(f"""SELECT section_id AS id, avg(speed) AS mean, count(*) AS n FROM section_live
                           WHERE section_id IN ({_in(ids)}) AND ts < ? AND speed IS NOT NULL
                             AND {self._HOUR} GROUP BY id""", (*ids, before, hour))
        return {r["id"]: (r["mean"], r["n"]) for r in rows}

    def camera_baselines(self, ids: list[str], hour: int, before: float) -> dict[str, tuple[float, float, int]]:
        if not ids:
            return {}
        rows = self._q(f"""SELECT camera_id AS id, avg(total) AS mean, avg(total*total) AS sq, count(*) AS n
                           FROM vision WHERE camera_id IN ({_in(ids)}) AND ts < ? AND {self._HOUR}
                           GROUP BY id""", (*ids, before, hour))
        out = {}
        for r in rows:
            var = max(0.0, (r["sq"] or 0) - (r["mean"] or 0) ** 2)
            out[r["id"]] = (r["mean"], var ** 0.5, r["n"])
        return out
