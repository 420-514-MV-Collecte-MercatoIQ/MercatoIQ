from datetime import datetime, timezone
import pandas as pd
from config import BRONZE_DATASETS, GOLD_COLLECTIONS, SILVER_COLLECTIONS
from gold_common import save_gold_docs
from silver_transform import clean_records_for_mongo


def _build_club_lookup(clubs_df: pd.DataFrame) -> dict[int, tuple]:
    """Map club_id -> (club_name, competition_id) for quick lookups."""
    lookup = {}
    if clubs_df is None or clubs_df.empty:
        return lookup

    for row in clubs_df.to_dict(orient="records"):
        cid = pd.to_numeric(row.get("club_id"), errors="coerce")
        if pd.isna(cid):
            continue
        name = row.get("name")
        comp_id = row.get("domestic_competition_id") or row.get("competition_id")
        lookup[int(cid)] = (name, comp_id)
    return lookup


def _summarize_club_attack(appearances_df: pd.DataFrame) -> pd.DataFrame:
    """Sum goals and assists per club_id and season from silver_appearances."""
    if appearances_df is None or appearances_df.empty:
        return pd.DataFrame(columns=["club_id", "season", "total_goals", "total_assists"])

    app = appearances_df.copy()
    app["club_id"] = pd.to_numeric(app.get("club_id"), errors="coerce")
    app["season"] = pd.to_numeric(app.get("season"), errors="coerce")
    app = app.dropna(subset=["club_id", "season"]).copy()
    if app.empty:
        return pd.DataFrame(columns=["club_id", "season", "total_goals", "total_assists"])

    app["club_id"] = app["club_id"].astype(int)
    app["season"] = app["season"].astype(int)
    app["goals"] = pd.to_numeric(app.get("goals"), errors="coerce").fillna(0).astype(int)
    app["assists"] = (
        pd.to_numeric(app.get("assists"), errors="coerce").fillna(0).astype(int)
    )

    return app.groupby(["club_id", "season"], as_index=False).agg(
        total_goals=("goals", "sum"),
        total_assists=("assists", "sum"),
    )


def build_club_transfer_roi_df(
    transfers_df: pd.DataFrame,
    clubs_df: pd.DataFrame | None = None,
    appearances_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Question 1: Aggregate transfer spending, market value gain, ROI,
    and offensive output (G+A per million EUR spent) per club and season.
    """
    if transfers_df is None or transfers_df.empty:
        return pd.DataFrame()

    df = transfers_df.copy()

    fee_col = "transfer_fee_eur" if "transfer_fee_eur" in df.columns else "transfer_fee"
    mv_col = (
        "market_value_at_transfer_eur"
        if "market_value_at_transfer_eur" in df.columns
        else "market_value_in_eur"
    )

    df["fee_eur"] = pd.to_numeric(df.get(fee_col), errors="coerce").fillna(0.0)
    df["market_value_eur"] = pd.to_numeric(df.get(mv_col), errors="coerce").fillna(0.0)
    df["value_gain_eur"] = df["market_value_eur"] - df["fee_eur"]

    if "to_club_name" not in df.columns:
        df["to_club_name"] = None

    grouped = df.groupby(["to_club_id", "season"], as_index=False).agg(
        players_bought=("player_id", "count"),
        total_fees_paid_eur=("fee_eur", "sum"),
        total_value_gain_eur=("value_gain_eur", "sum"),
        fallback_club_name=("to_club_name", "first"),
    )
    grouped = grouped.rename(columns={"to_club_id": "club_id"})
    grouped["club_id"] = grouped["club_id"].astype(int)
    grouped["season"] = grouped["season"].astype(int)

    # Merge offensive output from appearances when provided
    attack_df = _summarize_club_attack(appearances_df)
    grouped = grouped.merge(attack_df, on=["club_id", "season"], how="left")
    grouped["total_goals"] = grouped["total_goals"].fillna(0).astype(int)
    grouped["total_assists"] = grouped["total_assists"].fillna(0).astype(int)
    grouped["total_ga"] = grouped["total_goals"] + grouped["total_assists"]

    club_lookup = _build_club_lookup(clubs_df)
    club_names = []
    comp_ids = []
    rois = []
    ga_per_m = []

    for row in grouped.to_dict(orient="records"):
        cid = int(row["club_id"])
        name, comp_id = club_lookup.get(cid, (None, None))
        if not name:
            name = row.get("fallback_club_name")
        club_names.append(name)
        comp_ids.append(comp_id)

        fees = float(row["total_fees_paid_eur"])
        gain = float(row["total_value_gain_eur"])
        total_ga = int(row["total_ga"])

        rois.append(round(gain / fees, 4) if fees > 0 else None)
        ga_per_m.append(round(total_ga / (fees / 1_000_000.0), 4) if fees > 0 else None)

    grouped["club_name"] = club_names
    grouped["competition_id"] = comp_ids
    grouped["roi"] = rois
    grouped["ga_per_m_eur"] = ga_per_m

    # Club name is required by the GoldClubTransferRoi schema
    grouped = grouped.dropna(subset=["club_name"]).copy()
    grouped["roi"] = pd.Series(grouped["roi"], index=grouped.index, dtype=object)
    grouped["ga_per_m_eur"] = pd.Series(
        grouped["ga_per_m_eur"], index=grouped.index, dtype=object
    )

    cols = [
        "club_id",
        "club_name",
        "season",
        "competition_id",
        "total_fees_paid_eur",
        "total_value_gain_eur",
        "roi",
        "players_bought",
        "total_goals",
        "total_assists",
        "total_ga",
        "ga_per_m_eur",
    ]
    return grouped[cols].reset_index(drop=True)


def run_gold_club_roi(db) -> dict:
    """Read Silver/Bronze collections, compute Q1 Club ROI, and save to Gold."""
    transfers_df = pd.DataFrame(
        list(
            db[SILVER_COLLECTIONS["transfers"]].find(
                {},
                {
                    "_id": 0,
                    "player_id": 1,
                    "to_club_id": 1,
                    "to_club_name": 1,
                    "season": 1,
                    "transfer_fee_eur": 1,
                    "transfer_fee": 1,
                    "market_value_at_transfer_eur": 1,
                    "market_value_in_eur": 1,
                },
            )
        )
    )
    clubs_df = pd.DataFrame(
        list(
            db[BRONZE_DATASETS["clubs"]["collection"]].find(
                {}, {"_id": 0, "club_id": 1, "name": 1, "domestic_competition_id": 1}
            )
        )
    )
    appearances_df = pd.DataFrame(
        list(
            db[SILVER_COLLECTIONS["appearances"]].find(
                {}, {"_id": 0, "club_id": 1, "season": 1, "goals": 1, "assists": 1}
            )
        )
    )

    roi_df = build_club_transfer_roi_df(transfers_df, clubs_df, appearances_df)
    docs = clean_records_for_mongo(roi_df)
    now_utc = datetime.now(timezone.utc)
    for doc in docs:
        doc.pop("_silver_updated_at", None)
        doc["computed_at"] = now_utc

    written = save_gold_docs(
        db[GOLD_COLLECTIONS["club_roi"]],
        docs,
        key_fields=["club_id", "season"],
    )
    print(f"[gold] gold_club_transfer_roi: read={len(roi_df)}, written={written}")
    return {"read": len(roi_df), "written": written}
