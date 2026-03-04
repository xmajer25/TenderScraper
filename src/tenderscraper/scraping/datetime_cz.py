from __future__ import annotations

from datetime import datetime, timezone


def parse_cz_datetime(value: str) -> datetime | None:
    """
    Parses '25. 02. 2026 12:00:00' or '06. 02. 2026 14:17:26'.
    Returns timezone-aware UTC datetime (no TZ info in source -> assume local, you can change later).
    """
    if not value:
        return None

    s = " ".join(value.replace("\xa0", " ").split())
    # Normalize "25. 02. 2026 12:00:00" -> "25.02.2026 12:00:00"
    s = s.replace(". ", ".").replace(" .", ".")
    try:
        dt = datetime.strptime(s, "%d.%m.%Y %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
