from config import get_db
from gold_club_roi import run_gold_club_roi
from gold_common import ensure_gold_indexes
from gold_scouting_u23 import run_gold_scouting_u23
from gold_super_subs import run_gold_super_subs
from gold_ucl_vs_domestic import run_gold_ucl_vs_domestic


def run_gold_layer(db=None) -> dict:
    """
    Execute all four Gold analytical aggregations & ML scouting:
      - Q1: gold_club_transfer_roi
      - Q2: gold_undervalued_scouting (scikit-learn U23 fair value)
      - Q3: gold_ucl_vs_domestic
      - Q4: gold_super_subs
    """
    if db is None:
        db = get_db()

    ensure_gold_indexes(db)

    roi_stats = run_gold_club_roi(db)
    scouting_stats = run_gold_scouting_u23(db)
    ucl_dom_stats = run_gold_ucl_vs_domestic(db)
    super_subs_stats = run_gold_super_subs(db)

    collections = {
        "gold_club_transfer_roi": roi_stats,
        "gold_undervalued_scouting": scouting_stats,
        "gold_ucl_vs_domestic": ucl_dom_stats,
        "gold_super_subs": super_subs_stats,
    }
    total_processed = (
        roi_stats["read"]
        + scouting_stats["read"]
        + ucl_dom_stats["read"]
        + super_subs_stats["read"]
    )
    return {"total_processed": total_processed, "collections": collections}


if __name__ == "__main__":
    run_gold_layer()
