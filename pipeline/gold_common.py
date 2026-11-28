from pymongo import ASCENDING, DESCENDING, UpdateOne
from config import GOLD_COLLECTIONS


def ensure_gold_indexes(db):
    """Create unique and ranking indexes across all four Gold analytical collections."""
    club_roi = db[GOLD_COLLECTIONS["club_roi"]]
    club_roi.create_index([("club_id", ASCENDING), ("season", ASCENDING)], unique=True)
    club_roi.create_index([("season", ASCENDING), ("roi", DESCENDING)])

    ucl_dom = db[GOLD_COLLECTIONS["ucl_vs_domestic"]]
    ucl_dom.create_index([("player_id", ASCENDING), ("season", ASCENDING)], unique=True)
    ucl_dom.create_index([("season", ASCENDING), ("goals_per_90_delta", DESCENDING)])
    ucl_dom.create_index(
        [
            ("domestic_competition_id", ASCENDING),
            ("season", ASCENDING),
            ("position", ASCENDING),
        ]
    )

    scouting = db[GOLD_COLLECTIONS["undervalued_scouting"]]
    scouting.create_index([("player_id", ASCENDING), ("season", ASCENDING)], unique=True)
    scouting.create_index(
        [
            ("competition_id", ASCENDING),
            ("season", ASCENDING),
            ("undervalue_ratio", DESCENDING),
        ]
    )
    scouting.create_index(
        [("season", ASCENDING), ("position", ASCENDING), ("undervalue_ratio", DESCENDING)]
    )

    super_subs = db[GOLD_COLLECTIONS["super_subs"]]
    super_subs.create_index(
        [("player_id", ASCENDING), ("season", ASCENDING), ("competition_id", ASCENDING)],
        unique=True,
    )
    super_subs.create_index(
        [
            ("competition_id", ASCENDING),
            ("season", ASCENDING),
            ("super_sub_score", DESCENDING),
        ]
    )
    super_subs.create_index(
        [("season", ASCENDING), ("position", ASCENDING), ("super_sub_score", DESCENDING)]
    )

    print("[gold] indexes ready for all 4 gold collections")


def save_gold_docs(
    collection, docs: list[dict], key_fields: list[str], batch_size: int = 1000
) -> int:
    """Upsert Gold documents in batches so pipeline re-runs stay idempotent."""
    if not docs:
        return 0

    written = 0
    for start in range(0, len(docs), batch_size):
        chunk = docs[start : start + batch_size]
        ops = [
            UpdateOne(
                {field: doc[field] for field in key_fields},
                {"$set": doc},
                upsert=True,
            )
            for doc in chunk
        ]
        result = collection.bulk_write(ops, ordered=False)
        written += result.upserted_count + result.modified_count

    return written
