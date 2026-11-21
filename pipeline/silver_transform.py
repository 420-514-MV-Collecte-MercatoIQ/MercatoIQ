from datetime import datetime, timezone
import numpy as np
import pandas as pd
from pymongo import ASCENDING, DESCENDING, UpdateOne
from config import BRONZE_DATASETS, SILVER_COLLECTIONS


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


def _bulk_upsert(collection, docs: list[dict], key_field: str, batch_size: int = 2000) -> int:
    if not docs:
        return 0
    written = 0
    for i in range(0, len(docs), batch_size):
        chunk = docs[i : i + batch_size]
        ops = [
            UpdateOne({key_field: doc[key_field]}, {"$set": doc}, upsert=True)
            for doc in chunk
        ]
        res = collection.bulk_write(ops, ordered=False)
        written += res.upserted_count + res.modified_count
    return written


def build_silver_players_df(
    players_df: pd.DataFrame,
    clubs_df: pd.DataFrame,
    valuations_df: pd.DataFrame,
    max_valuations: int = 10,
) -> pd.DataFrame:
    """
    Build normalized silver_players documents.
    Embedded design choice: embed current_club summary and latest market_valuations
    inside each player document so profile reads need zero joins.
    """
    if players_df is None or players_df.empty:
        return pd.DataFrame()

    df = players_df.copy()
    df["player_id"] = pd.to_numeric(df.get("player_id"), errors="coerce")
    df = df.dropna(subset=["player_id"]).copy()
    if df.empty:
        return pd.DataFrame()

    df["player_id"] = df["player_id"].astype(int)
    df = df.drop_duplicates(subset=["player_id"], keep="last").reset_index(drop=True)

    for col in ("current_club_id", "height_in_cm", "last_season"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = np.nan

    for col in ("market_value_in_eur", "highest_market_value_in_eur"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
        else:
            df[col] = np.nan

    dob_dt = pd.to_datetime(df.get("date_of_birth"), errors="coerce")
    df["date_of_birth"] = dob_dt.dt.strftime("%Y-%m-%d").where(dob_dt.notna(), None)
    now_ts = pd.Timestamp.utcnow().tz_localize(None)
    age_series = ((now_ts - dob_dt).dt.days / 365.25).round(1)
    df["age"] = age_series.where(dob_dt.notna(), None)

    if "position" in df.columns:
        df["position"] = (
            df["position"].fillna("Unknown").astype(str).str.strip().replace("", "Unknown")
        )
    else:
        df["position"] = "Unknown"

    if "sub_position" in df.columns:
        df["sub_position"] = df["sub_position"].where(df["sub_position"].notna(), None)
    else:
        df["sub_position"] = None

    # Build club lookup for embedding current_club summary
    club_lookup = {}
    if clubs_df is not None and not clubs_df.empty:
        c_df = clubs_df.copy()
        c_df["club_id"] = pd.to_numeric(c_df.get("club_id"), errors="coerce")
        c_df = c_df.dropna(subset=["club_id"])
        for row in c_df.to_dict(orient="records"):
            cid = int(row["club_id"])
            comp = row.get("domestic_competition_id") or row.get("competition_id")
            club_lookup[cid] = {
                "club_id": cid,
                "name": _to_native_python(row.get("name")),
                "competition_id": _to_native_python(comp),
            }

    # Group recent market valuations per player
    valuations_map: dict[int, list[dict]] = {}
    if valuations_df is not None and not valuations_df.empty:
        v_df = valuations_df.copy()
        v_df["player_id"] = pd.to_numeric(v_df.get("player_id"), errors="coerce")
        v_df["market_value_in_eur"] = pd.to_numeric(
            v_df.get("market_value_in_eur"), errors="coerce"
        )
        v_df["date_dt"] = pd.to_datetime(v_df.get("date"), errors="coerce")
        v_df = v_df.dropna(subset=["player_id", "market_value_in_eur", "date_dt"]).copy()
        if not v_df.empty:
            v_df["player_id"] = v_df["player_id"].astype(int)
            v_df["date_str"] = v_df["date_dt"].dt.strftime("%Y-%m-%d")
            v_df["current_club_id"] = pd.to_numeric(
                v_df.get("current_club_id"), errors="coerce"
            )
            v_df = v_df.sort_values(["player_id", "date_str"], ascending=[True, False])

            for pid, grp in v_df.groupby("player_id", sort=False):
                snapshots = []
                for r in grp.head(max_valuations).to_dict(orient="records"):
                    cid_val = r.get("current_club_id")
                    comp_val = r.get("player_club_domestic_competition_id") or r.get(
                        "competition_id"
                    )
                    snapshots.append(
                        {
                            "date": r["date_str"],
                            "market_value_in_eur": float(r["market_value_in_eur"]),
                            "club_id": int(cid_val) if pd.notna(cid_val) else None,
                            "competition_id": _to_native_python(comp_val),
                        }
                    )
                valuations_map[int(pid)] = snapshots

    embedded_clubs = []
    embedded_vals = []
    for row in df.to_dict(orient="records"):
        pid = int(row["player_id"])
        cid_raw = row.get("current_club_id")
        cid = int(cid_raw) if pd.notna(cid_raw) else None
        fallback_name = _to_native_python(row.get("current_club_name"))
        fallback_comp = _to_native_python(
            row.get("current_club_domestic_competition_id")
        )

        if cid is not None and cid in club_lookup:
            club_obj = dict(club_lookup[cid])
            if not club_obj.get("name") and fallback_name:
                club_obj["name"] = fallback_name
            if not club_obj.get("competition_id") and fallback_comp:
                club_obj["competition_id"] = fallback_comp
        elif cid is not None or fallback_name is not None:
            club_obj = {
                "club_id": cid,
                "name": fallback_name,
                "competition_id": fallback_comp,
            }
        else:
            club_obj = None

        embedded_clubs.append(club_obj)
        embedded_vals.append(valuations_map.get(pid, []))

    df["current_club"] = embedded_clubs
    df["market_valuations"] = embedded_vals

    output_cols = [
        "player_id",
        "name",
        "first_name",
        "last_name",
        "date_of_birth",
        "age",
        "country_of_citizenship",
        "position",
        "sub_position",
        "foot",
        "height_in_cm",
        "market_value_in_eur",
        "highest_market_value_in_eur",
        "last_season",
        "image_url",
        "current_club",
        "market_valuations",
    ]
    for col in output_cols:
        if col not in df.columns:
            df[col] = None

    return df[output_cols]


def transform_silver_players(db, batch_size: int = 2000) -> dict:
    """Transform bronze_players + bronze_clubs + bronze_player_valuations into silver_players."""
    proj = {"_id": 0, "_ingested_at": 0, "_source": 0}
    players_df = pd.DataFrame(
        list(db[BRONZE_DATASETS["players"]["collection"]].find({}, proj))
    )
    clubs_df = pd.DataFrame(
        list(db[BRONZE_DATASETS["clubs"]["collection"]].find({}, proj))
    )
    valuations_df = pd.DataFrame(
        list(db[BRONZE_DATASETS["player_valuations"]["collection"]].find({}, proj))
    )

    silver_df = build_silver_players_df(players_df, clubs_df, valuations_df)
    docs = clean_records_for_mongo(silver_df)
    written = _bulk_upsert(
        db[SILVER_COLLECTIONS["players"]],
        docs,
        key_field="player_id",
        batch_size=batch_size,
    )
    print(f"[silver] silver_players: read={len(silver_df)}, written={written}")
    return {"read": len(silver_df), "written": written}


def build_silver_appearances_df(
    appearances_df: pd.DataFrame,
    players_meta_df: pd.DataFrame,
    games_meta_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build normalized silver_appearances documents with referenced IDs,
    age_at_match, and vectorized per_90 offensive metrics.
    """
    if appearances_df is None or appearances_df.empty:
        return pd.DataFrame()

    df = appearances_df.copy()
    if "appearance_id" not in df.columns:
        return pd.DataFrame()

    df = df.dropna(subset=["appearance_id"]).copy()
    df["appearance_id"] = df["appearance_id"].astype(str).str.strip()
    df = df[df["appearance_id"] != ""].drop_duplicates(subset=["appearance_id"], keep="last")

    df["player_id"] = pd.to_numeric(df.get("player_id"), errors="coerce")
    df["game_id"] = pd.to_numeric(df.get("game_id"), errors="coerce")
    df = df.dropna(subset=["player_id", "game_id"]).copy()
    if df.empty:
        return pd.DataFrame()

    df["player_id"] = df["player_id"].astype(int)
    df["game_id"] = df["game_id"].astype(int)

    club_col = "player_club_id" if "player_club_id" in df.columns else "club_id"
    df["club_id"] = pd.to_numeric(df.get(club_col), errors="coerce")

    df["minutes_played"] = (
        pd.to_numeric(df.get("minutes_played"), errors="coerce")
        .fillna(0)
        .clip(lower=0, upper=120)
        .astype(int)
    )
    for stat_col in ("goals", "assists", "yellow_cards", "red_cards"):
        df[stat_col] = (
            pd.to_numeric(df.get(stat_col), errors="coerce")
            .fillna(0)
            .clip(lower=0)
            .astype(int)
        )

    # Merge lightweight player metadata (date_of_birth, position)
    if players_meta_df is not None and not players_meta_df.empty:
        p_meta = players_meta_df[["player_id", "date_of_birth", "position"]].copy()
        p_meta["player_id"] = pd.to_numeric(p_meta["player_id"], errors="coerce")
        p_meta = p_meta.dropna(subset=["player_id"]).drop_duplicates(subset=["player_id"])
        p_meta["player_id"] = p_meta["player_id"].astype(int)
        df = df.merge(p_meta, on="player_id", how="left", suffixes=("", "_p"))
    else:
        df["date_of_birth"] = None
        df["position"] = "Unknown"

    df["position"] = (
        df["position"].fillna("Unknown").astype(str).str.strip().replace("", "Unknown")
    )

    # Merge game season and infer from match date when missing
    if games_meta_df is not None and not games_meta_df.empty:
        g_meta = games_meta_df[["game_id", "season"]].copy()
        g_meta["game_id"] = pd.to_numeric(g_meta["game_id"], errors="coerce")
        g_meta["season"] = pd.to_numeric(g_meta["season"], errors="coerce")
        g_meta = g_meta.dropna(subset=["game_id"]).drop_duplicates(subset=["game_id"])
        g_meta["game_id"] = g_meta["game_id"].astype(int)
        df = df.merge(g_meta, on="game_id", how="left", suffixes=("", "_g"))
    elif "season" not in df.columns:
        df["season"] = np.nan
    else:
        df["season"] = pd.to_numeric(df["season"], errors="coerce")

    match_dt = pd.to_datetime(df.get("date"), errors="coerce")
    df["date"] = match_dt.dt.strftime("%Y-%m-%d").where(match_dt.notna(), None)

    missing_season_mask = df["season"].isna() & match_dt.notna()
    if missing_season_mask.any():
        inferred = np.where(
            match_dt.dt.month >= 7, match_dt.dt.year, match_dt.dt.year - 1
        )
        df.loc[missing_season_mask, "season"] = inferred[missing_season_mask]

    # Compute age_at_match with pandas datetime arithmetic
    dob_dt = pd.to_datetime(df["date_of_birth"], errors="coerce")
    age_at_match = ((match_dt - dob_dt).dt.days / 365.25).round(2)
    valid_age_mask = match_dt.notna() & dob_dt.notna() & (age_at_match >= 0)
    df["age_at_match"] = age_at_match.where(valid_age_mask, None)

    # Compute vectorized per_90 metrics with NumPy
    mins = df["minutes_played"].to_numpy(dtype=float)
    goals_arr = df["goals"].to_numpy(dtype=float)
    assists_arr = df["assists"].to_numpy(dtype=float)

    df["goals_per_90"] = np.where(mins > 0, np.round((goals_arr / mins) * 90.0, 4), 0.0)
    df["assists_per_90"] = np.where(
        mins > 0, np.round((assists_arr / mins) * 90.0, 4), 0.0
    )
    df["ga_per_90"] = np.round(df["goals_per_90"] + df["assists_per_90"], 4)

    for int_col in ("club_id", "season"):
        df[int_col] = pd.Series(
            [int(v) if pd.notna(v) else None for v in df[int_col]],
            index=df.index,
            dtype=object,
        )

    output_cols = [
        "appearance_id",
        "player_id",
        "game_id",
        "club_id",
        "competition_id",
        "season",
        "position",
        "date",
        "player_name",
        "age_at_match",
        "minutes_played",
        "goals",
        "assists",
        "yellow_cards",
        "red_cards",
        "goals_per_90",
        "assists_per_90",
        "ga_per_90",
    ]
    for col in output_cols:
        if col not in df.columns:
            df[col] = None

    return df[output_cols].reset_index(drop=True)


def transform_silver_appearances(db, chunk_size: int = 10000) -> dict:
    """Stream bronze_appearances in batches and upsert normalized silver_appearances."""
    p_docs = list(
        db[SILVER_COLLECTIONS["players"]].find(
            {}, {"_id": 0, "player_id": 1, "date_of_birth": 1, "position": 1}
        )
    )
    if not p_docs:
        p_docs = list(
            db[BRONZE_DATASETS["players"]["collection"]].find(
                {}, {"_id": 0, "player_id": 1, "date_of_birth": 1, "position": 1}
            )
        )
    players_meta_df = pd.DataFrame(p_docs)

    g_docs = list(
        db[BRONZE_DATASETS["games"]["collection"]].find(
            {}, {"_id": 0, "game_id": 1, "season": 1}
        )
    )
    games_meta_df = pd.DataFrame(g_docs)

    bronze_col = db[BRONZE_DATASETS["appearances"]["collection"]]
    silver_col = db[SILVER_COLLECTIONS["appearances"]]

    total_read = 0
    total_written = 0
    batch = []

    cursor = bronze_col.find({}, {"_id": 0, "_ingested_at": 0, "_source": 0}).batch_size(
        chunk_size
    )
    for row in cursor:
        batch.append(row)
        if len(batch) >= chunk_size:
            silver_df = build_silver_appearances_df(
                pd.DataFrame(batch), players_meta_df, games_meta_df
            )
            docs = clean_records_for_mongo(silver_df)
            total_read += len(batch)
            total_written += _bulk_upsert(
                silver_col, docs, key_field="appearance_id", batch_size=chunk_size
            )
            batch.clear()

    if batch:
        silver_df = build_silver_appearances_df(
            pd.DataFrame(batch), players_meta_df, games_meta_df
        )
        docs = clean_records_for_mongo(silver_df)
        total_read += len(batch)
        total_written += _bulk_upsert(
            silver_col, docs, key_field="appearance_id", batch_size=chunk_size
        )

    print(f"[silver] silver_appearances: read={total_read}, written={total_written}")
    return {"read": total_read, "written": total_written}
