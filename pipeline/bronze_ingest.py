from datetime import datetime, timezone
import math
import pandas as pd
from pymongo import ASCENDING, UpdateOne
from config import BRONZE_DATASETS, MIN_SEASON, TARGET_COMPETITIONS, get_db
from downloader import get_dataset_file


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

TARGET_COMP_IDS = set(TARGET_COMPETITIONS.keys())


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
    if pd.isna(val):
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


def filter_chunk(df: pd.DataFrame, dataset_name: str, valid_game_ids=None) -> pd.DataFrame:
    """Keep Top 5 leagues + UCL from MIN_SEASON onwards so we don't load unrelated leagues."""
    min_date_str = f"{MIN_SEASON}-01-01"

    if dataset_name == "competitions":
        return df[df["competition_id"].isin(TARGET_COMP_IDS)]

    if dataset_name == "games":
        season_num = pd.to_numeric(df["season"], errors="coerce")
        return df[(df["competition_id"].isin(TARGET_COMP_IDS)) & (season_num >= MIN_SEASON)]

    if dataset_name == "appearances":
        dates = df["date"].astype(str)
        return df[(df["competition_id"].isin(TARGET_COMP_IDS)) & (dates >= min_date_str)]

    if dataset_name == "game_events":
        if valid_game_ids:
            return df[df["game_id"].isin(valid_game_ids)]
        dates = df["date"].astype(str)
        return df[dates >= min_date_str]

    if dataset_name == "player_valuations":
        dates = df["date"].astype(str)
        return df[dates >= min_date_str]

    if dataset_name == "transfers":
        dates = df["transfer_date"].astype(str)
        return df[dates >= min_date_str]

    return df


def ingest_dataset(
    db,
    dataset_name: str,
    offline: bool = False,
    batch_size: int = 5000,
    limit: int = None,
    valid_game_ids=None,
) -> dict:
    """Read a single CSV/GZ in chunks and bulk upsert it into its bronze_* collection."""
    meta = BRONZE_DATASETS[dataset_name]
    col = db[meta["collection"]]
    unique_keys = meta["unique_key"]

    file_path, source_label = get_dataset_file(dataset_name, offline=offline)

    total_read = 0
    total_upserted = 0
    skipped = 0
    columns_checked = False
    collected_game_ids = set()

    # read in chunks otherwise appearances / game_events eat way too much ram
    reader = pd.read_csv(file_path, chunksize=batch_size, low_memory=False)
    for chunk in reader:
        if not columns_checked:
            validate_columns(chunk.columns, dataset_name)
            columns_checked = True

        filtered = filter_chunk(chunk, dataset_name, valid_game_ids=valid_game_ids)
        if filtered.empty:
            continue

        if limit is not None:
            remaining = limit - total_read
            if remaining <= 0:
                break
            filtered = filtered.iloc[:remaining]

        if dataset_name == "games":
            collected_game_ids.update(filtered["game_id"].dropna().tolist())

        # deduplicate keys inside the same batch before bulk_write (avoids E11000 errors)
        batch_docs = {}
        for raw_row in filtered.to_dict(orient="records"):
            total_read += 1
            doc = prepare_bronze_doc(raw_row, dataset_name, source_label=source_label)
            if doc is None:
                skipped += 1
                continue
            key_tuple = tuple(doc[k] for k in unique_keys)
            batch_docs[key_tuple] = doc

        if not batch_docs:
            continue

        ops = [
            UpdateOne(
                {k: doc[k] for k in unique_keys},
                {"$set": doc},
                upsert=True,
            )
            for doc in batch_docs.values()
        ]
        res = col.bulk_write(ops, ordered=False)
        total_upserted += res.upserted_count + res.modified_count

        if limit is not None and total_read >= limit:
            break

    print(
        f"[bronze] {meta['collection']}: read={total_read}, "
        f"written={total_upserted}, skipped={skipped} (source={source_label})"
    )
    return {
        "collection": meta["collection"],
        "source": source_label,
        "read": total_read,
        "written": total_upserted,
        "skipped": skipped,
        "collected_game_ids": collected_game_ids,
    }


def run_bronze_layer(db=None, offline: bool = False, limit: int = None, tables=None) -> dict:
    """Run Bronze ingestion for all configured datasets (or a subset)."""
    if db is None:
        db = get_db()

    ensure_bronze_indexes(db)

    selected = tables if tables else list(BRONZE_DATASETS.keys())
    summary = {"total_processed": 0, "tables": {}}
    valid_game_ids = set()

    for name in selected:
        stats = ingest_dataset(
            db,
            dataset_name=name,
            offline=offline,
            limit=limit,
            valid_game_ids=valid_game_ids if name == "game_events" else None,
        )
        if name == "games":
            valid_game_ids = stats["collected_game_ids"]

        # don't dump huge sets into pipeline_logs
        stats.pop("collected_game_ids", None)
        summary["tables"][name] = stats
        summary["total_processed"] += stats["read"]

    return summary


if __name__ == "__main__":
    run_bronze_layer()
