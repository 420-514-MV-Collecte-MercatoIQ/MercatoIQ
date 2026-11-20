from datetime import datetime, timezone
import math
from pymongo import ASCENDING
from config import BRONZE_DATASETS


# minimum columns expected in each raw file so we catch broken csvs early
REQUIRED_COLUMNS = {
    "competitions": ["competition_id", "name"],
    "clubs": ["club_id", "name"],
    "players": ["player_id", "name"],
    "games": ["game_id", "competition_id", "season"],
    "appearances": ["appearance_id", "game_id", "player_id", "competition_id"],
    "game_events": ["game_event_id", "game_id", "minute", "type"],
    "player_valuations": ["player_id", "date", "market_value_in_eur"],
    "transfers": ["player_id", "transfer_date", "from_club_id", "to_club_id"],
}


def ensure_bronze_indexes(db):
    """Make sure every bronze collection has a unique index on its source ID."""
    for name, meta in BRONZE_DATASETS.items():
        col = db[meta["collection"]]
        key_spec = [(field, ASCENDING) for field in meta["unique_key"]]
        col.create_index(key_spec, unique=True)
        print(f"[bronze] unique index ready on {meta['collection']} -> {meta['unique_key']}")


def make_transfer_id(row: dict) -> str:
    """
    transfers.csv doesn't come with a transfer_id column from transfermarkt,
    so we build a deterministic key from player + date + clubs to keep upserts idempotent.
    """
    pid = row.get("player_id")
    tdate = row.get("transfer_date")
    from_cid = row.get("from_club_id")
    to_cid = row.get("to_club_id")
    if pid is None or not tdate:
        return ""
    return f"{pid}_{tdate}_{from_cid}_{to_cid}"


def _clean_value(val):
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    if isinstance(val, str):
        stripped = val.strip()
        return stripped if stripped != "" else None
    return val


def prepare_bronze_doc(raw_row: dict, dataset_name: str, source_label: str = "remote"):
    """Clean NaNs, attach transfer_id if needed, and verify primary key exists."""
    doc = {k: _clean_value(v) for k, v in raw_row.items()}

    if dataset_name == "transfers" and not doc.get("transfer_id"):
        tid = make_transfer_id(doc)
        doc["transfer_id"] = tid if tid else None

    unique_keys = BRONZE_DATASETS[dataset_name]["unique_key"]
    for key in unique_keys:
        if doc.get(key) is None:
            return None

    doc["_ingested_at"] = datetime.now(timezone.utc)
    doc["_source"] = source_label
    return doc


def validate_columns(columns, dataset_name: str):
    expected = set(REQUIRED_COLUMNS.get(dataset_name, []))
    missing = expected - set(columns)
    if missing:
        raise ValueError(
            f"Dataset '{dataset_name}' is missing required columns: {sorted(missing)}"
        )
