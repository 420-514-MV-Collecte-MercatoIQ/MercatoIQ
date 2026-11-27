from datetime import datetime, timezone
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from config import BRONZE_DATASETS, GOLD_COLLECTIONS, SILVER_COLLECTIONS, TARGET_COMPETITIONS
from gold_common import save_gold_docs
from silver_transform import clean_records_for_mongo

DOMESTIC_LEAGUES = {"GB1", "ES1", "IT1", "L1", "FR1"}

NUMERIC_FEATURES = [
    "age",
    "minutes_played",
    "appearances",
    "goals_per_90",
    "assists_per_90",
    "ga_per_90",
    "yellow_cards",
    "red_cards",
    "season",
]
CATEGORICAL_FEATURES = ["position", "competition_id"]


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
        series = df[col] if col in df.columns else pd.Series(0, index=df.index)
        df[col] = pd.to_numeric(series, errors="coerce").fillna(0).clip(lower=0).astype(int)

    age_series = df["age_at_match"] if "age_at_match" in df.columns else pd.Series(np.nan, index=df.index)
    df["age_at_match"] = pd.to_numeric(age_series, errors="coerce")
    club_series = df["club_id"] if "club_id" in df.columns else pd.Series(np.nan, index=df.index)
    df["club_id"] = pd.to_numeric(club_series, errors="coerce")
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
    return (
        pd.DataFrame(records)
        .sort_values(["season", "minutes_played"], ascending=[False, False])
        .reset_index(drop=True)
    )


def _build_preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), NUMERIC_FEATURES),
            ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ]
    )


def fit_and_predict_u23_values(feature_df: pd.DataFrame) -> tuple[np.ndarray, dict]:
    """
    Compare Ridge regression vs RandomForestRegressor on log1p(market_value_eur),
    selecting the best cross-validated estimator on R2 (and falling back cleanly on small inputs).
    """
    if feature_df.empty:
        return np.array([], dtype=float), {"model": "none", "r2": None, "mae_eur": None}

    X = feature_df[NUMERIC_FEATURES + CATEGORICAL_FEATURES].copy()
    y_eur = feature_df["market_value_eur"].to_numpy(dtype=float)
    y_log = np.log1p(y_eur)

    n_samples = len(feature_df)
    if n_samples < 5:
        pipe = Pipeline([("prep", _build_preprocessor()), ("reg", Ridge(alpha=2.0))])
        pipe.fit(X, y_log)
        preds_eur = np.maximum(50_000.0, np.expm1(pipe.predict(X)))
        metrics = {
            "model": "Ridge(alpha=2.0)",
            "r2": round(float(r2_score(y_eur, preds_eur)), 4) if n_samples >= 2 else None,
            "mae_eur": round(float(mean_absolute_error(y_eur, preds_eur)), 2),
        }
        return preds_eur, metrics

    candidates = {
        "Ridge": Pipeline([("prep", _build_preprocessor()), ("reg", Ridge(alpha=5.0))]),
        "RandomForestRegressor": Pipeline(
            [
                ("prep", _build_preprocessor()),
                (
                    "reg",
                    RandomForestRegressor(
                        n_estimators=80,
                        max_depth=6,
                        min_samples_leaf=3,
                        random_state=42,
                        n_jobs=-1,
                    ),
                ),
            ]
        ),
    }

    n_splits = min(5, n_samples)
    cv = KFold(n_splits=n_splits, shuffle=True, random_state=42)

    best_name = "Ridge"
    best_r2 = float("-inf")
    best_preds_eur = y_eur
    best_mae = float("inf")

    for name, estimator in candidates.items():
        oof_log = cross_val_predict(estimator, X, y_log, cv=cv)
        oof_eur = np.maximum(50_000.0, np.expm1(oof_log))
        score_r2 = float(r2_score(y_log, oof_log))
        score_mae = float(mean_absolute_error(y_eur, oof_eur))
        if score_r2 > best_r2:
            best_name = name
            best_r2 = score_r2
            best_mae = score_mae
            best_preds_eur = oof_eur

    metrics = {
        "model": best_name,
        "r2": round(best_r2, 4),
        "mae_eur": round(best_mae, 2),
    }
    return best_preds_eur, metrics


def build_scouting_u23_df(
    players_df: pd.DataFrame,
    appearances_df: pd.DataFrame,
    clubs_df: pd.DataFrame | None = None,
    min_minutes: int = 450,
    max_age: int = 23,
) -> tuple[pd.DataFrame, dict]:
    """
    Question 2 (ML): Estimate fair transfer market value for U23 players and rank
    undervalued scouting targets by `undervalue_ratio = estimated_value_eur / market_value_eur`.
    """
    feat_df = prepare_u23_feature_df(
        players_df=players_df,
        appearances_df=appearances_df,
        clubs_df=clubs_df,
        min_minutes=min_minutes,
        max_age=max_age,
    )
    if feat_df.empty:
        return pd.DataFrame(), {"model": "none", "r2": None, "mae_eur": None}

    preds_eur, metrics = fit_and_predict_u23_values(feat_df)
    rounded_preds = np.round(preds_eur, -3)

    out_df = feat_df.copy()
    out_df["estimated_value_eur"] = rounded_preds.astype(float)
    out_df["undervalue_ratio"] = np.round(
        out_df["estimated_value_eur"] / out_df["market_value_eur"], 4
    )
    out_df["club_id"] = pd.Series(out_df["club_id"], index=out_df.index, dtype=object)

    cols = [
        "player_id",
        "player_name",
        "season",
        "competition_id",
        "position",
        "market_value_eur",
        "estimated_value_eur",
        "undervalue_ratio",
        "goals_per_90",
        "assists_per_90",
        "minutes_played",
        "age",
        "club_id",
        "club_name",
    ]
    return (
        out_df[cols].sort_values("undervalue_ratio", ascending=False).reset_index(drop=True),
        metrics,
    )


def run_gold_scouting_u23(db) -> dict:
    """Read Silver/Bronze collections, train U23 fair-value ML model, and save to Gold."""
    players_df = pd.DataFrame(
        list(
            db[SILVER_COLLECTIONS["players"]].find(
                {},
                {
                    "_id": 0,
                    "player_id": 1,
                    "player_name": 1,
                    "name": 1,
                    "position": 1,
                    "date_of_birth": 1,
                    "age": 1,
                    "current_club": 1,
                    "current_club_id": 1,
                    "current_club_name": 1,
                    "current_market_value_eur": 1,
                    "market_value_in_eur": 1,
                },
            )
        )
    )
    appearances_df = pd.DataFrame(
        list(
            db[SILVER_COLLECTIONS["appearances"]].find(
                {},
                {
                    "_id": 0,
                    "player_id": 1,
                    "season": 1,
                    "competition_id": 1,
                    "club_id": 1,
                    "age_at_match": 1,
                    "minutes_played": 1,
                    "goals": 1,
                    "assists": 1,
                    "yellow_cards": 1,
                    "red_cards": 1,
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

    scouting_df, ml_metrics = build_scouting_u23_df(players_df, appearances_df, clubs_df)
    docs = clean_records_for_mongo(scouting_df)
    now_utc = datetime.now(timezone.utc)
    for doc in docs:
        doc.pop("_silver_updated_at", None)
        doc["computed_at"] = now_utc

    written = save_gold_docs(
        db[GOLD_COLLECTIONS["undervalued_scouting"]],
        docs,
        key_fields=["player_id", "season"],
    )
    print(
        f"[gold] gold_undervalued_scouting: read={len(scouting_df)}, written={written}, "
        f"model={ml_metrics.get('model')}, r2={ml_metrics.get('r2')}, mae_eur={ml_metrics.get('mae_eur')}"
    )
    return {"read": len(scouting_df), "written": written, "ml_metrics": ml_metrics}
