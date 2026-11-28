from datetime import datetime, timezone
import numpy as np
import pandas as pd
from config import (
    BRONZE_DATASETS,
    GOLD_COLLECTIONS,
    SILVER_COLLECTIONS,
    TARGET_COMPETITIONS,
)
from gold_common import save_gold_docs
from gold_ucl_vs_domestic import _build_club_names, _build_player_lookup, calc_per_90
from silver_transform import clean_records_for_mongo


def classify_sub_appearances(
    appearances_df: pd.DataFrame,
    game_events_df: pd.DataFrame | None = None,
    fallback_max_sub_minutes: int = 40,
) -> pd.DataFrame:
    """
    Tag each appearance with `is_sub` and `sub_on_minute` using Substitutions events
    (`player_in_id == player_id` on matching `game_id`). Falls back cleanly when event rows
    are omitted in lightweight inputs.
    """
    df = appearances_df.copy()
    df["player_id"] = pd.to_numeric(df.get("player_id"), errors="coerce")
    df["game_id"] = pd.to_numeric(df.get("game_id"), errors="coerce")
    df["season"] = pd.to_numeric(df.get("season"), errors="coerce")
    df = df.dropna(subset=["player_id", "game_id", "season", "competition_id"]).copy()
    if df.empty:
        return pd.DataFrame()

    df["player_id"] = df["player_id"].astype(int)
    df["game_id"] = df["game_id"].astype(int)
    df["season"] = df["season"].astype(int)
    for col in ("goals", "assists", "minutes_played"):
        series = df[col] if col in df.columns else pd.Series(0, index=df.index)
        df[col] = pd.to_numeric(series, errors="coerce").fillna(0).clip(lower=0).astype(int)
    club_series = df["club_id"] if "club_id" in df.columns else pd.Series(np.nan, index=df.index)
    df["club_id"] = pd.to_numeric(club_series, errors="coerce")

    sub_lookup: dict[tuple[int, int], int] = {}
    has_events = False
    if game_events_df is not None and not game_events_df.empty:
        ev = game_events_df.copy()
        if "type" in ev.columns:
            ev = ev[ev["type"].astype(str).str.lower() == "substitutions"]
        ev["game_id"] = pd.to_numeric(ev["game_id"] if "game_id" in ev.columns else pd.Series(np.nan, index=ev.index), errors="coerce")
        ev["player_in_id"] = pd.to_numeric(ev["player_in_id"] if "player_in_id" in ev.columns else pd.Series(np.nan, index=ev.index), errors="coerce")
        min_series = ev["minute"] if "minute" in ev.columns else pd.Series(75, index=ev.index)
        ev["minute"] = pd.to_numeric(min_series, errors="coerce").fillna(75).astype(int)
        ev = ev.dropna(subset=["game_id", "player_in_id"]).copy()
        if not ev.empty:
            has_events = True
            for row in ev.to_dict(orient="records"):
                key = (int(row["game_id"]), int(row["player_in_id"]))
                minute_val = int(row["minute"])
                if key not in sub_lookup or minute_val < sub_lookup[key]:
                    sub_lookup[key] = minute_val

    is_sub_flags = []
    sub_minutes = []
    for row in df.to_dict(orient="records"):
        key = (int(row["game_id"]), int(row["player_id"]))
        if has_events:
            if key in sub_lookup:
                is_sub_flags.append(True)
                sub_minutes.append(sub_lookup[key])
            else:
                is_sub_flags.append(False)
                sub_minutes.append(None)
        else:
            mins = int(row["minutes_played"])
            likely_sub = 0 < mins <= fallback_max_sub_minutes
            is_sub_flags.append(likely_sub)
            sub_minutes.append(max(1, 90 - mins) if likely_sub else None)

    df["is_sub"] = is_sub_flags
    df["sub_on_minute"] = sub_minutes
    df["is_late_sub"] = [
        bool(s and m is not None and m >= 70) for s, m in zip(is_sub_flags, sub_minutes)
    ]
    return df


def compute_super_sub_score(
    sub_ga_per_90: float | None,
    starter_ga_per_90: float | None,
    sub_appearances: int,
    late_sub_appearances: int,
) -> float | None:
    """
    Score substitute impact in [0, 100] combining bench G+A/90 output, positive lift vs
    starter baseline, late-game (>=70th min) frequency, and sample volume.
    """
    if sub_ga_per_90 is None or sub_appearances <= 0:
        return None

    bench_rate = max(0.0, float(sub_ga_per_90))
    starter_rate = max(0.0, float(starter_ga_per_90)) if starter_ga_per_90 is not None else 0.0
    uplift = max(0.0, bench_rate - starter_rate)
    late_ratio = late_sub_appearances / float(sub_appearances)
    confidence = min(1.0, sub_appearances / 12.0)

    raw_score = (
        (min(bench_rate, 2.5) / 2.5) * 58.0
        + (min(uplift, 1.5) / 1.5) * 22.0
        + late_ratio * 10.0
        + confidence * 10.0
    )
    return round(min(100.0, max(0.0, raw_score)), 2)


def build_super_subs_df(
    appearances_df: pd.DataFrame,
    game_events_df: pd.DataFrame | None = None,
    players_df: pd.DataFrame | None = None,
    clubs_df: pd.DataFrame | None = None,
    min_sub_apps: int = 3,
    min_sub_minutes: int = 60,
) -> pd.DataFrame:
    """
    Question 4: Quantify bench vs starter per-90 goal contributions (highlighting >70th
    minute substitutions) per (player_id, season, competition_id).
    """
    if appearances_df is None or appearances_df.empty:
        return pd.DataFrame()

    tagged = classify_sub_appearances(appearances_df, game_events_df)
    if tagged.empty:
        return pd.DataFrame()

    tagged = tagged[tagged["competition_id"].isin(TARGET_COMPETITIONS.keys())].copy()
    if tagged.empty:
        return pd.DataFrame()

    player_lookup = _build_player_lookup(players_df)
    club_names = _build_club_names(clubs_df)

    rows = []
    for (pid, season, comp_id), grp in tagged.groupby(
        ["player_id", "season", "competition_id"], sort=False
    ):
        subs = grp[grp["is_sub"]]
        sub_apps = int(len(subs))
        sub_mins = int(subs["minutes_played"].sum())
        if sub_apps < min_sub_apps or sub_mins < min_sub_minutes:
            continue

        starters = grp[~grp["is_sub"]]
        starter_apps = int(len(starters))
        starter_mins = int(starters["minutes_played"].sum())

        sub_goals = int(subs["goals"].sum())
        sub_assists = int(subs["assists"].sum())
        starter_goals = int(starters["goals"].sum())
        starter_assists = int(starters["assists"].sum())
        late_sub_apps = int(subs["is_late_sub"].sum())

        sub_ga90 = calc_per_90(sub_goals + sub_assists, sub_mins)
        starter_ga90 = calc_per_90(starter_goals + starter_assists, starter_mins)
        score = compute_super_sub_score(sub_ga90, starter_ga90, sub_apps, late_sub_apps)

        p_name, p_pos = player_lookup.get(int(pid), (None, None))
        last_name = (
            grp["player_name"].dropna().iloc[-1]
            if "player_name" in grp.columns and not grp["player_name"].dropna().empty
            else None
        )
        last_pos = (
            grp["position"].dropna().iloc[-1]
            if "position" in grp.columns and not grp["position"].dropna().empty
            else None
        )
        club_vals = grp["club_id"].dropna()
        club_id = int(club_vals.iloc[-1]) if not club_vals.empty else None
        club_name = club_names.get(club_id) if club_id is not None else None

        rows.append(
            {
                "player_id": int(pid),
                "player_name": p_name or last_name or f"Player #{int(pid)}",
                "season": int(season),
                "competition_id": str(comp_id),
                "position": p_pos or last_pos or "Unknown",
                "club_id": club_id,
                "club_name": club_name,
                "sub_appearances": sub_apps,
                "sub_goals": sub_goals,
                "sub_assists": sub_assists,
                "sub_minutes_played": sub_mins,
                "sub_goal_contributions_per_90": sub_ga90,
                "starter_appearances": starter_apps,
                "starter_goals": starter_goals,
                "starter_assists": starter_assists,
                "starter_minutes_played": starter_mins,
                "starter_goal_contributions_per_90": starter_ga90,
                "super_sub_score": score,
            }
        )

    if not rows:
        return pd.DataFrame()

    out_df = pd.DataFrame(rows)
    for col in (
        "club_id",
        "sub_goal_contributions_per_90",
        "starter_goal_contributions_per_90",
        "super_sub_score",
    ):
        out_df[col] = pd.Series(out_df[col], index=out_df.index, dtype=object)

    return out_df.sort_values(
        ["super_sub_score", "sub_goal_contributions_per_90"],
        ascending=[False, False],
        na_position="last",
    ).reset_index(drop=True)


def run_gold_super_subs(db) -> dict:
    """Read Silver appearances & Bronze substitutions, compute Q4 Super-Subs, and save to Gold."""
    appearances_df = pd.DataFrame(
        list(
            db[SILVER_COLLECTIONS["appearances"]].find(
                {"competition_id": {"$in": list(TARGET_COMPETITIONS.keys())}},
                {
                    "_id": 0,
                    "player_id": 1,
                    "player_name": 1,
                    "game_id": 1,
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
    game_events_df = pd.DataFrame(
        list(
            db[BRONZE_DATASETS["game_events"]["collection"]].find(
                {"type": "Substitutions"},
                {"_id": 0, "game_id": 1, "minute": 1, "type": 1, "player_in_id": 1},
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

    subs_df = build_super_subs_df(appearances_df, game_events_df, players_df, clubs_df)
    docs = clean_records_for_mongo(subs_df)
    now_utc = datetime.now(timezone.utc)
    for doc in docs:
        doc.pop("_silver_updated_at", None)
        doc["computed_at"] = now_utc

    written = save_gold_docs(
        db[GOLD_COLLECTIONS["super_subs"]],
        docs,
        key_fields=["player_id", "season", "competition_id"],
    )
    print(f"[gold] gold_super_subs: read={len(subs_df)}, written={written}")
    return {"read": len(subs_df), "written": written}
