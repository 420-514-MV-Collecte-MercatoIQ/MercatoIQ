import pandas as pd

DOMESTIC_LEAGUES = {"GB1", "ES1", "IT1", "L1", "FR1"}


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
