"""Read-only bridge into the self-hosted GoatCounter SQLite DB for the
Back Office Analytics tab. See docs/analytics.md for how tracking is wired up.

GoatCounter (a separate process, systemd unit `goatcounter.service`) owns and
writes this database; we only ever open it read-only and never write to it.
"""

import asyncio
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.core.config import settings


@contextmanager
def _connect():
    uri = f"file:{settings.goatcounter_db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _read_stats(days: int) -> dict:
    if not Path(settings.goatcounter_db_path).exists():
        return {"available": False}

    with _connect() as conn:
        site_id = conn.execute("select site_id from sites limit 1").fetchone()
        if not site_id:
            return {"available": False}
        site_id = site_id["site_id"]

        totals = conn.execute(
            """
            select
              sum(total) filter (where hour > datetime('now','-1 day')) as last_24h,
              sum(total) filter (where hour > datetime('now','-7 days')) as last_7d,
              sum(total) filter (where hour > datetime('now','-30 days')) as last_30d,
              sum(total) as all_time
            from hit_counts where site_id = ?
            """,
            (site_id,),
        ).fetchone()

        daily = conn.execute(
            """
            select date(hour) as day, sum(total) as total
            from hit_counts
            where site_id = ? and hour > datetime('now', ?)
            group by date(hour)
            order by day asc
            """,
            (site_id, f"-{days} days"),
        ).fetchall()

        top_paths = conn.execute(
            """
            select p.path, p.title, sum(h.total) as total
            from hit_counts h join paths p on p.path_id = h.path_id
            where h.site_id = ? and p.event = 0
            group by p.path_id
            order by total desc
            limit 15
            """,
            (site_id,),
        ).fetchall()

        top_refs = conn.execute(
            """
            select r.ref, sum(rc.total) as total
            from ref_counts rc join refs r on r.ref_id = rc.ref_id
            where rc.site_id = ? and r.ref != ''
            group by r.ref_id
            order by total desc
            limit 15
            """,
            (site_id,),
        ).fetchall()

        browsers = conn.execute(
            """
            select b.name, sum(bs.count) as total
            from browser_stats bs join browsers b on b.browser_id = bs.browser_id
            where bs.site_id = ?
            group by b.name
            order by total desc
            limit 8
            """,
            (site_id,),
        ).fetchall()

        systems = conn.execute(
            """
            select s.name, sum(ss.count) as total
            from system_stats ss join systems s on s.system_id = ss.system_id
            where ss.site_id = ?
            group by s.name
            order by total desc
            limit 8
            """,
            (site_id,),
        ).fetchall()

        locations = conn.execute(
            """
            select location, sum(count) as total
            from location_stats
            where site_id = ? and location != ''
            group by location
            order by total desc
            limit 12
            """,
            (site_id,),
        ).fetchall()

        sizes = conn.execute(
            """
            select
              case
                when width < 768 then 'mobile'
                when width < 1024 then 'tablet'
                else 'desktop'
              end as bucket,
              sum(count) as total
            from size_stats
            where site_id = ? and width > 0
            group by bucket
            """,
            (site_id,),
        ).fetchall()

        return {
            "available": True,
            "totals": dict(totals) if totals else {},
            "daily": [dict(r) for r in daily],
            "top_paths": [dict(r) for r in top_paths],
            "top_referrers": [dict(r) for r in top_refs],
            "browsers": [dict(r) for r in browsers],
            "systems": [dict(r) for r in systems],
            "locations": [dict(r) for r in locations],
            "sizes": [dict(r) for r in sizes],
        }


async def get_goatcounter_stats(days: int = 14) -> dict:
    return await asyncio.to_thread(_read_stats, days)
