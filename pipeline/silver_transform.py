from datetime import datetime, timezone
import numpy as np
import pandas as pd
from pymongo import ASCENDING, DESCENDING
from config import SILVER_COLLECTIONS


def ensure_silver_indexes(db):
    """Create unique and compound indexes on all Silver collections."""
    players_col = db[SILVER_COLLECTIONS["players"]]
    players_col.create_index([("player_id", ASCENDING)], unique=True)
    players_col.create_index(
        [
            ("position", ASCENDING),
            ("current_club.competition_id", ASCENDING),
            ("current_club.club_id", ASCENDING),
        ]
    )

    appearances_col = db[SILVER_COLLECTIONS["appearances"]]
    appearances_col.create_index([("appearance_id", ASCENDING)], unique=True)
    appearances_col.create_index(
        [
            ("competition_id", ASCENDING),
            ("season", ASCENDING),
            ("player_id", ASCENDING),
            ("position", ASCENDING),
        ]
    )
    appearances_col.create_index([("player_id", ASCENDING), ("season", DESCENDING)])

    transfers_col = db[SILVER_COLLECTIONS["transfers"]]
    transfers_col.create_index([("transfer_id", ASCENDING)], unique=True)
    transfers_col.create_index(
        [("season", ASCENDING), ("to_club_id", ASCENDING), ("player_id", ASCENDING)]
    )
    print("[silver] unique & compound indexes ready")


def infer_season_from_date(date_val) -> int | None:
    """Derive football season start year from YYYY-MM-DD (Jul-Dec -> same year, Jan-Jun -> year-1)."""
    if date_val is None or (isinstance(date_val, float) and np.isnan(date_val)):
        return None
    dt = pd.to_datetime(date_val, errors="coerce")
    if pd.isna(dt):
        return None
    return int(dt.year) if dt.month >= 7 else int(dt.year) - 1


def parse_transfer_season(season_val, date_val=None) -> int | None:
    """Convert Transfermarkt season labels like '23/24', '99/00', or '2022' into start year int."""
    if season_val is not None and not (isinstance(season_val, float) and np.isnan(season_val)):
        raw = str(season_val).strip()
        if "/" in raw:
            left = raw.split("/")[0].strip()
            if left.isdigit():
                yr = int(left)
                if len(left) == 4:
                    return yr
                return 2000 + yr if yr < 50 else 1900 + yr
        if raw.isdigit():
            yr = int(raw)
            if yr >= 1900:
                return yr
            return 2000 + yr if yr < 50 else 1900 + yr

    return infer_season_from_date(date_val)


def _to_native_python(val):
    if val is None:
        return None
    if isinstance(val, (np.floating, float)):
        return None if np.isnan(val) else float(val)
    if isinstance(val, np.integer):
        return int(val)
    if isinstance(val, np.bool_):
        return bool(val)
    if isinstance(val, pd.Timestamp):
        return None if pd.isna(val) else val.to_pydatetime()
    if isinstance(val, list):
        return [_to_native_python(v) for v in val]
    if isinstance(val, dict):
        return {k: _to_native_python(v) for k, v in val.items()}
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return val


def clean_records_for_mongo(df: pd.DataFrame) -> list[dict]:
    """Convert a pandas DataFrame into Mongo-safe dicts (no NaN/NA/numpy scalars)."""
    if df.empty:
        return []
    now_utc = datetime.now(timezone.utc)
    records = []
    for row in df.to_dict(orient="records"):
        doc = {k: _to_native_python(v) for k, v in row.items()}
        doc["_silver_updated_at"] = now_utc
        records.append(doc)
    return records
