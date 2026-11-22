#!/usr/bin/env python3
"""Quick helper script to verify Bronze row counts and unique indexes in MongoDB."""
import sys
from config import BRONZE_DATASETS, get_db

MIN_UNIQUE_OBSERVATIONS = 100_000


def main():
    db = get_db()
    total_docs = 0
    all_indexes_ok = True

    print("=== MercatoIQ Bronze Layer Check ===")
    for name, meta in BRONZE_DATASETS.items():
        col_name = meta["collection"]
        expected_keys = meta["unique_key"]
        col = db[col_name]

        count = col.count_documents({})
        total_docs += count

        # check that a unique index exists on expected_keys
        idx_info = col.index_information()
        has_unique = any(
            info.get("unique") and [k[0] for k in info.get("key", [])] == expected_keys
            for info in idx_info.values()
        )
        if not has_unique:
            all_indexes_ok = False

        status = "OK" if has_unique else "MISSING UNIQUE INDEX"
        print(f"  {col_name:<26} | docs: {count:>10,} | key={expected_keys} [{status}]")

    print("-" * 72)
    print(f"Total unique Bronze documents: {total_docs:,}")

    if total_docs >= MIN_UNIQUE_OBSERVATIONS:
        print(f"[PASS] > {MIN_UNIQUE_OBSERVATIONS:,} unique observations requirement met.")
    else:
        print(f"[WARN] Below {MIN_UNIQUE_OBSERVATIONS:,} observations target (did you run full bronze ingest?).")

    if not all_indexes_ok:
        print("[FAIL] Some collections are missing their unique index.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
