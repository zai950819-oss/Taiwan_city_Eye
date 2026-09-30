"""Tiny WKT parser (LINESTRING / MULTILINESTRING) — avoids a shapely dependency."""
from __future__ import annotations

import re

_PAREN = re.compile(r"\(([^()]+)\)")


def parse_wkt_lines(wkt: str) -> list[list[list[float]]]:
    if not wkt:
        return []
    lines = []
    for group in _PAREN.findall(wkt):
        pts = []
        for pair in group.split(","):
            parts = pair.split()
            if len(parts) >= 2:
                try:
                    pts.append([round(float(parts[0]), 6), round(float(parts[1]), 6)])
                except ValueError:
                    pass
        if len(pts) >= 2:
            lines.append(pts)
    return lines
