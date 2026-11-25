from datetime import datetime, timezone
import pandas as pd
from config import BRONZE_DATASETS, GOLD_COLLECTIONS, SILVER_COLLECTIONS
from gold_common import save_gold_docs
from silver_transform import clean_records_for_mongo

DOMESTIC_LEAGUES = {"GB1", "ES1", "IT1", "L1", "FR1"}


def calc_per_90(stat_value: int | float, minutes: int | float) -> float | None:
    """Simple per-90 rate helper returning None when zero minutes were played."""
    if not minutes or minutes <= 0:
        return None
    return round((float(stat_value) / float(minutes)) * 90.0, 4)


def summarize_ucl_and_domestic(appearances_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split silver_appearances into UEFA Champions League ('CL') and Top-5
    domestic league totals per (player_id, season).
    """
    if appearances_df is None or appearances_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    df = appearances_df.copy()
    df["player_id"] = pd.to_numeric(df.get("player_id"), errors="coerce")
    df["season"] = pd.to_numeric(df.get("season"), errors="coerce")
    df = df.dropna(subset=["player_id", "season", "competition_id"]).copy()
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    df["player_id"] = df["player_id"].astype(int)
    df["season"] = df["season"].astype(int)
    df["goals"] = pd.to_numeric(df.get("goals"), errors="coerce").fillna(0).astype(int)
    df["assists"] = pd.to_numeric(df.get("assists"), errors="coerce").fillna(0).astype(int)
    df["minutes_played"] = (
        pd.to_numeric(df.get("minutes_played"), errors="coerce").fillna(0).astype(int)
    )

    for opt_col in ("club_id", "player_name", "position"):
        if opt_col not in df.columns:
            df[opt_col] = None

    ucl_rows = df[df["competition_id"] == "CL"]
    ucl_grouped = ucl_rows.groupby(["player_id", "season"], as_index=False).agg(
        ucl_appearances=("goals", "count"),
        ucl_goals=("goals", "sum"),
        ucl_assists=("assists", "sum"),
        ucl_minutes_played=("minutes_played", "sum"),
    )

    dom_rows = df[df["competition_id"].isin(DOMESTIC_LEAGUES)]
    dom_grouped = dom_rows.groupby(["player_id", "season"], as_index=False).agg(
        domestic_competition_id=("competition_id", "first"),
        club_id=("club_id", "last"),
        player_name=("player_name", "last"),
        position=("position", "last"),
        domestic_appearances=("goals", "count"),
        domestic_goals=("goals", "sum"),
        domestic_assists=("assists", "sum"),
        domestic_minutes_played=("minutes_played", "sum"),
    )

    return ucl_grouped, dom_grouped


def _build_player_lookup(players_df: pd.DataFrame | None) -> dict[int, tuple]:
    lookup = {}
    if players_df is None or players_df.empty:
        return lookup
    for row in players_df.to_dict(orient="records"):
        pid = pd.to_numeric(row.get("player_id"), errors="coerce")
        if pd.isna(pid):
            continue
        name = row.get("player_name") or row.get("name")
        lookup[int(pid)] = (name, row.get("position"))
    return lookup


def _build_club_names(clubs_df: pd.DataFrame | None) -> dict[int, str]:
    lookup = {}
    if clubs_df is None or clubs_df.empty:
        return lookup
    for row in clubs_df.to_dict(orient="records"):
        cid = pd.to_numeric(row.get("club_id"), errors="coerce")
        if pd.isna(cid):
            continue
        lookup[int(cid)] = row.get("name")
    return lookup


def build_ucl_vs_domestic_df(
    appearances_df: pd.DataFrame,
    players_df: pd.DataFrame | None = None,
    clubs_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Question 3: Compare per-90 output in UCL vs Top-5 domestic leagues
    for every player active in both competitions in the same season.
    """
    ucl_df, dom_df = summarize_ucl_and_domestic(appearances_df)
    if ucl_df.empty or dom_df.empty:
        return pd.DataFrame()

    merged = ucl_df.merge(dom_df, on=["player_id", "season"], how="inner")
    if merged.empty:
        return pd.DataFrame()

    player_lookup = _build_player_lookup(players_df)
    club_names = _build_club_names(clubs_df)

    rows = []
    for r in merged.to_dict(orient="records"):
        pid = int(r["player_id"])
        p_name, p_pos = player_lookup.get(pid, (None, None))
        player_name = p_name or r.get("player_name") or f"Player #{pid}"
        position = p_pos or r.get("position") or "Unknown"

        cid_val = pd.to_numeric(r.get("club_id"), errors="coerce")
        club_id = int(cid_val) if pd.notna(cid_val) else None
        club_name = club_names.get(club_id) if club_id is not None else None

        ucl_g90 = calc_per_90(r["ucl_goals"], r["ucl_minutes_played"])
        dom_g90 = calc_per_90(r["domestic_goals"], r["domestic_minutes_played"])
        g90_delta = (
            round(ucl_g90 - dom_g90, 4)
            if (ucl_g90 is not None and dom_g90 is not None)
            else None
        )

        ucl_ga90 = calc_per_90(r["ucl_goals"] + r["ucl_assists"], r["ucl_minutes_played"])
        dom_ga90 = calc_per_90(
            r["domestic_goals"] + r["domestic_assists"], r["domestic_minutes_played"]
        )
        ga90_delta = (
            round(ucl_ga90 - dom_ga90, 4)
            if (ucl_ga90 is not None and dom_ga90 is not None)
            else None
        )

        rows.append(
            {
                "player_id": pid,
                "player_name": player_name,
                "season": int(r["season"]),
                "position": position,
                "club_id": club_id,
                "club_name": club_name,
                "ucl_appearances": int(r["ucl_appearances"]),
                "ucl_goals": int(r["ucl_goals"]),
                "ucl_assists": int(r["ucl_assists"]),
                "ucl_minutes_played": int(r["ucl_minutes_played"]),
                "ucl_goals_per_90": ucl_g90,
                "ucl_ga_per_90": ucl_ga90,
                "domestic_competition_id": r["domestic_competition_id"],
                "domestic_appearances": int(r["domestic_appearances"]),
                "domestic_goals": int(r["domestic_goals"]),
                "domestic_assists": int(r["domestic_assists"]),
                "domestic_minutes_played": int(r["domestic_minutes_played"]),
                "domestic_goals_per_90": dom_g90,
                "domestic_ga_per_90": dom_ga90,
                "goals_per_90_delta": g90_delta,
                "ga_per_90_delta": ga90_delta,
            }
        )

    out_df = pd.DataFrame(rows)
    for col in (
        "club_id",
        "ucl_goals_per_90",
        "ucl_ga_per_90",
        "domestic_goals_per_90",
        "domestic_ga_per_90",
        "goals_per_90_delta",
        "ga_per_90_delta",
    ):
        out_df[col] = pd.Series(out_df[col], index=out_df.index, dtype=object)

    return out_df


def run_gold_ucl_vs_domestic(db) -> dict:
    """Read Silver/Bronze collections, compute Q3 UCL vs Domestic, and save to Gold."""
    appearances_df = pd.DataFrame(
        list(
            db[SILVER_COLLECTIONS["appearances"]].find(
                {},
                {
                    "_id": 0,
                    "player_id": 1,
                    "player_name": 1,
                    "club_id": 1,
                    "competition_id": 1,
                    "season": 1,
                    "position": 1,
                    "goals": 1,
                    "assists": 1,
                    "minutes_played": 1,
                },
            )
        )
    )
    players_df = pd.DataFrame(
        list(
            db[SILVER_COLLECTIONS["players"]].find(
                {}, {"_id": 0, "player_id": 1, "player_name": 1, "name": 1, "position": 1}
            )
        )
    )
    clubs_df = pd.DataFrame(
        list(
            db[BRONZE_DATASETS["clubs"]["collection"]].find(
                {}, {"_id": 0, "club_id": 1, "name": 1}
            )
        )
    )

    ucl_dom_df = build_ucl_vs_domestic_df(appearances_df, players_df, clubs_df)
    docs = clean_records_for_mongo(ucl_dom_df)
    now_utc = datetime.now(timezone.utc)
    for doc in docs:
        doc.pop("_silver_updated_at", None)
        doc["computed_at"] = now_utc

    written = save_gold_docs(
        db[GOLD_COLLECTIONS["ucl_vs_domestic"]],
        docs,
        key_fields=["player_id", "season"],
    )
    print(f"[gold] gold_ucl_vs_domestic: read={len(ucl_dom_df)}, written={written}")
    return {"read": len(ucl_dom_df), "written": written}
