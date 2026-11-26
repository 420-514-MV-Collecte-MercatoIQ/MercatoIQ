from config import get_db
from gold_club_roi import run_gold_club_roi
from gold_common import ensure_gold_indexes
from gold_ucl_vs_domestic import run_gold_ucl_vs_domestic


def run_gold_layer(db=None) -> dict:
    """
    Execute Week 2 Gold aggregations:
      - Q1: gold_club_transfer_roi
      - Q3: gold_ucl_vs_domestic
    (Q2 U23 ML scouting & Q4 super-subs are scheduled for Week 3 in PIPE-03c.)
    """
    if db is None:
        db = get_db()

    ensure_gold_indexes(db)

    roi_stats = run_gold_club_roi(db)
    ucl_dom_stats = run_gold_ucl_vs_domestic(db)

    collections = {
        "gold_club_transfer_roi": roi_stats,
        "gold_ucl_vs_domestic": ucl_dom_stats,
    }
    total_processed = roi_stats["read"] + ucl_dom_stats["read"]
    return {"total_processed": total_processed, "collections": collections}


if __name__ == "__main__":
    run_gold_layer()
