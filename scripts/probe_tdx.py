"""Print raw TDX samples to verify field names with your keys:  python scripts/probe_tdx.py"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx  # noqa: E402

from godeye.config import settings  # noqa: E402
from godeye.tdx import TDXClient  # noqa: E402

PATHS = ["v2/Road/Traffic/CCTV/Freeway", "v2/Road/Traffic/CCTV/City/Taipei", "v2/Road/Traffic/Live/Freeway",
         "v2/Road/Traffic/SectionShape/Freeway", "v2/Road/Traffic/Section/Freeway"]


async def main():
    if not settings.has_tdx_keys:
        sys.exit("Set TDX_CLIENT_ID / TDX_CLIENT_SECRET in .env first")
    async with httpx.AsyncClient(timeout=30) as http:
        tdx = TDXClient(settings.tdx_client_id, settings.tdx_client_secret, http)
        for p in PATHS:
            data = await tdx.get(p, **{"$top": 2})
            print(f"\n=== {p}\n{json.dumps(data, ensure_ascii=False, indent=1)[:1500]}")

asyncio.run(main())
