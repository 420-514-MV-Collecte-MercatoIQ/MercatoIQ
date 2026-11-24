import pandas as pd


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


def build_club_transfer_roi_df(
    transfers_df: pd.DataFrame,
    clubs_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Question 1: Aggregate transfer spending, market value gain, and ROI
    for each club and season.
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

    club_lookup = _build_club_lookup(clubs_df)
    club_names = []
    comp_ids = []
    rois = []

    for row in grouped.to_dict(orient="records"):
        cid = int(row["club_id"])
        name, comp_id = club_lookup.get(cid, (None, None))
        if not name:
            name = row.get("fallback_club_name")
        club_names.append(name)
        comp_ids.append(comp_id)

        fees = float(row["total_fees_paid_eur"])
        gain = float(row["total_value_gain_eur"])
        rois.append(round(gain / fees, 4) if fees > 0 else None)

    grouped["club_name"] = club_names
    grouped["competition_id"] = comp_ids
    grouped["roi"] = rois

    # Club name is required by the GoldClubTransferRoi schema
    grouped = grouped.dropna(subset=["club_name"]).copy()
    grouped["roi"] = pd.Series(grouped["roi"], index=grouped.index, dtype=object)

    cols = [
        "club_id",
        "club_name",
        "season",
        "competition_id",
        "total_fees_paid_eur",
        "total_value_gain_eur",
        "roi",
        "players_bought",
    ]
    return grouped[cols].reset_index(drop=True)
