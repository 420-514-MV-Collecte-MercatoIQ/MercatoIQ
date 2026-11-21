#!/usr/bin/env python3
import argparse
import sys
import time
import uuid
from datetime import datetime, timezone
from bronze_ingest import run_bronze_layer
from config import BACKUP_DATA_DIR, get_db


def parse_args():
    parser = argparse.ArgumentParser(
        description="MercatoIQ Data Pipeline Orchestrator (Bronze -> Silver -> Gold)"
    )
    parser.add_argument(
        "--trigger",
        choices=["cron", "manual", "crud"],
        default="manual",
        help="What triggered this pipeline run",
    )
    parser.add_argument(
        "--layer",
        choices=["all", "bronze", "silver", "gold"],
        default="all",
        help="Which medallion layer(s) to run",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip CDN downloads and read strictly from pipeline/backup_data/",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max rows per dataset (handy for quick dev tests)",
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
        "details": {},
    }
    db.pipeline_logs.insert_one(log_entry)
    print(
        f"[MercatoIQ Pipeline] Run {run_id} started "
        f"(trigger={args.trigger}, layer={args.layer}, offline={args.offline})"
    )

    try:
        records_processed = 0
        details = {}

        if args.layer in ("all", "bronze"):
            bronze_stats = run_bronze_layer(
                db=db,
                offline=args.offline,
                limit=args.limit,
            )
            records_processed += bronze_stats["total_processed"]
            details["bronze"] = bronze_stats["tables"]

        # TODO (PIPE-02): hook silver transformation here
        # TODO (PIPE-03): hook gold aggregations + ML model here

        duration_ms = int((time.perf_counter() - t0) * 1000)

        db.pipeline_logs.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    "status": "success",
                    "duration_ms": duration_ms,
                    "records_processed": records_processed,
                    "details": details,
                    "completed_at": datetime.now(timezone.utc),
                }
            },
        )
        print(
            f"[MercatoIQ Pipeline] Run {run_id} finished in {duration_ms}ms "
            f"({records_processed} records)"
        )
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
        print(f"[MercatoIQ Pipeline] Run {run_id} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
