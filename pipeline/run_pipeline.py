#!/usr/bin/env python3
import argparse
import sys
import time
import uuid
from datetime import datetime, timezone
from config import get_db, BACKUP_DATA_DIR


def parse_args():
    parser = argparse.ArgumentParser(description="Orchestrateur du pipeline MercatoIQ (Bronze -> Silver -> Gold)")
    parser.add_argument(
        "--trigger",
        choices=["cron", "manual", "crud"],
        default="manual",
        help="Origine du declenchement du pipeline",
    )
    parser.add_argument(
        "--layer",
        choices=["all", "bronze", "silver", "gold"],
        default="all",
        help="Couche(s) a executer (ex: 'gold' lors d'une mutation CRUD)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    BACKUP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = get_db()

    log_entry = {
        "run_id": run_id,
        "trigger_type": args.trigger,
        "layer": args.layer,
        "status": "running",
        "started_at": started_at,
        "duration_ms": 0,
        "records_processed": 0,
        "error_message": None,
    }
    db.pipeline_logs.insert_one(log_entry)
    print(f"[MercatoIQ Pipeline] Run {run_id} demarre (trigger={args.trigger}, layer={args.layer})")

    try:
        records_processed = 0
        # Les modules bronze / silver / gold seront branches dans PIPE-01, PIPE-02, PIPE-03
        duration_ms = int((time.perf_counter() - t0) * 1000)

        db.pipeline_logs.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    "status": "success",
                    "duration_ms": duration_ms,
                    "records_processed": records_processed,
                    "completed_at": datetime.now(timezone.utc),
                }
            },
        )
        print(f"[MercatoIQ Pipeline] Run {run_id} termine avec succes en {duration_ms}ms")
        return 0

    except Exception as exc:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        db.pipeline_logs.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    "status": "failed",
                    "duration_ms": duration_ms,
                    "error_message": str(exc),
                    "completed_at": datetime.now(timezone.utc),
                }
            },
        )
        print(f"[MercatoIQ Pipeline] Erreur sur le run {run_id}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
