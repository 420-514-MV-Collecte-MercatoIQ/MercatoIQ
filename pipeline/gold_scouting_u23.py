from datetime import datetime, timezone
import numpy as np
import pandas as pd
from config import TARGET_COMPETITIONS

DOMESTIC_LEAGUES = {"GB1", "ES1", "IT1", "L1", "FR1"}


def _build_club_lookup(clubs_df: pd.DataFrame | None) -> dict[int, tuple[str | None, str | None]]:
    """Map club_id -> (club_name, domestic_competition_id)."""
    lookup: dict[int, tuple[str | None, str | None]] = {}
    if clubs_df is None or clubs_df.empty:
        return lookup

    for row in clubs_df.to_dict(orient="records"):
        cid = pd.to_numeric(row.get("club_id"), errors="coerce")
        if pd.isna(cid):
            continue
        comp_id = row.get("domestic_competition_id") or row.get("competition_id")
        lookup[int(cid)] = (row.get("name") or row.get("club_name"), comp_id)
    return lookup


def _extract_player_metadata(players_df: pd.DataFrame | None) -> dict[int, dict]:
    """Build normalized metadata map keyed by player_id from silver_players."""
    meta: dict[int, dict] = {}
    if players_df is None or players_df.empty:
        return meta

    for row in players_df.to_dict(orient="records"):
        pid = pd.to_numeric(row.get("player_id"), errors="coerce")
        if pd.isna(pid):
            continue
        mv = pd.to_numeric(
            row.get("current_market_value_eur")
            if row.get("current_market_value_eur") is not None
            else (
                row.get("market_value_in_eur")
                if row.get("market_value_in_eur") is not None
                else row.get("market_value_eur")
            ),
            errors="coerce",
        )
        dob_dt = pd.to_datetime(row.get("date_of_birth"), errors="coerce")
        raw_age = pd.to_numeric(row.get("age"), errors="coerce")

        embedded_club = row.get("current_club")
        club_id = None
        club_name = None
        club_comp = None
        if isinstance(embedded_club, dict):
            c_raw = pd.to_numeric(embedded_club.get("club_id"), errors="coerce")
            club_id = int(c_raw) if pd.notna(c_raw) else None
            club_name = embedded_club.get("club_name") or embedded_club.get("name")
            club_comp = embedded_club.get("competition_id")
        else:
            c_raw = pd.to_numeric(row.get("current_club_id"), errors="coerce")
            club_id = int(c_raw) if pd.notna(c_raw) else None
            club_name = row.get("current_club_name")

        meta[int(pid)] = {
            "player_name": row.get("player_name") or row.get("name"),
            "position": row.get("position") or "Unknown",
            "market_value_eur": float(mv) if pd.notna(mv) else np.nan,
            "birth_year": int(dob_dt.year) if pd.notna(dob_dt) else None,
            "age": float(raw_age) if pd.notna(raw_age) else np.nan,
            "club_id": club_id,
            "club_name": club_name,
            "competition_id": club_comp,
        }
    return meta


def _pick_primary_competition(comp_minutes: dict[str, int], fallback: str | None) -> str | None:
    """Prefer the Top-5 domestic league with the most minutes over cup/UCL competitions."""
    if not comp_minutes:
        return fallback
    domestic_only = {k: v for k, v in comp_minutes.items() if k in DOMESTIC_LEAGUES}
    pool = domestic_only if domestic_only else comp_minutes
    return max(pool.items(), key=lambda kv: kv[1])[0]


def prepare_u23_feature_df(
    players_df: pd.DataFrame,
    appearances_df: pd.DataFrame,
    clubs_df: pd.DataFrame | None = None,
    min_minutes: int = 450,
    max_age: int = 23,
) -> pd.DataFrame:
    """
    Aggregate silver_appearances + silver_players into an ML-ready U23 player-season
    feature table with per-90 efficiency and positive market valuations.
    """
    if players_df is None or players_df.empty or appearances_df is None or appearances_df.empty:
        return pd.DataFrame()

    df = appearances_df.copy()
    df["player_id"] = pd.to_numeric(df.get("player_id"), errors="coerce")
    df["season"] = pd.to_numeric(df.get("season"), errors="coerce")
    df = df.dropna(subset=["player_id", "season"]).copy()
    if df.empty:
        return pd.DataFrame()

    df["player_id"] = df["player_id"].astype(int)
    df["season"] = df["season"].astype(int)

    for col in ("goals", "assists", "minutes_played", "yellow_cards", "red_cards"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce").fillna(0).clip(lower=0).astype(int)

    df["age_at_match"] = pd.to_numeric(df.get("age_at_match"), errors="coerce")
    df["club_id"] = pd.to_numeric(df.get("club_id"), errors="coerce")
    if "competition_id" not in df.columns:
        df["competition_id"] = None

    player_meta = _extract_player_metadata(players_df)
    club_lookup = _build_club_lookup(clubs_df)

    records = []
    for (pid, season), grp in df.groupby(["player_id", "season"], sort=False):
        p_info = player_meta.get(int(pid))
        if not p_info:
            continue

        market_value = p_info.get("market_value_eur")
        if market_value is None or not np.isfinite(market_value) or market_value <= 0:
            continue

        total_minutes = int(grp["minutes_played"].sum())
        if total_minutes < min_minutes:
            continue

        valid_ages = grp["age_at_match"].dropna()
        if not valid_ages.empty:
            raw_age = float(valid_ages.mean())
        elif p_info.get("birth_year"):
            raw_age = float(int(season) - int(p_info["birth_year"]))
        else:
            raw_age = p_info.get("age")

        if raw_age is None or not np.isfinite(raw_age) or raw_age <= 0 or raw_age > max_age + 0.99:
            continue

        comp_minutes: dict[str, int] = {}
        for c_id, mins in zip(grp["competition_id"], grp["minutes_played"]):
            if isinstance(c_id, str) and c_id in TARGET_COMPETITIONS:
                comp_minutes[c_id] = comp_minutes.get(c_id, 0) + int(mins)

        club_vals = grp["club_id"].dropna()
        club_id = int(club_vals.iloc[-1]) if not club_vals.empty else p_info.get("club_id")
        club_name, club_comp = club_lookup.get(club_id, (None, None)) if club_id else (None, None)
        if not club_name:
            club_name = p_info.get("club_name")

        competition_id = _pick_primary_competition(
            comp_minutes, club_comp or p_info.get("competition_id")
        )
        if not competition_id:
            non_null_comps = [c for c in grp["competition_id"] if isinstance(c, str) and c.strip()]
            competition_id = non_null_comps[0] if non_null_comps else None
        if not competition_id:
            continue

        total_goals = int(grp["goals"].sum())
        total_assists = int(grp["assists"].sum())
        appearances_count = int(len(grp))
        g90 = round((total_goals / total_minutes) * 90.0, 4)
        a90 = round((total_assists / total_minutes) * 90.0, 4)
        ga90 = round(((total_goals + total_assists) / total_minutes) * 90.0, 4)

        records.append(
            {
                "player_id": int(pid),
                "player_name": p_info.get("player_name") or f"Player #{pid}",
                "season": int(season),
                "competition_id": str(competition_id),
                "position": str(p_info.get("position") or "Unknown"),
                "age": int(round(raw_age)),
                "club_id": int(club_id) if club_id is not None else None,
                "club_name": club_name,
                "appearances": appearances_count,
                "minutes_played": total_minutes,
                "goals": total_goals,
                "assists": total_assists,
                "goals_per_90": g90,
                "assists_per_90": a90,
                "ga_per_90": ga90,
                "yellow_cards": int(grp["yellow_cards"].sum()),
                "red_cards": int(grp["red_cards"].sum()),
                "market_value_eur": float(market_value),
            }
        )

    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).sort_values(["season", "minutes_played"], ascending=[False, False]).reset_index(drop=True)
