import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

# allow running directly from repo root or pipeline/
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bronze_ingest
import downloader


class TestBronzeIngestion(unittest.TestCase):
    def test_make_transfer_id_is_deterministic(self):
        row = {
            "player_id": 12345,
            "transfer_date": "2024-07-01",
            "from_club_id": 10,
            "to_club_id": 20,
        }
        tid1 = bronze_ingest.make_transfer_id(row)
        tid2 = bronze_ingest.make_transfer_id(dict(row))
        self.assertEqual(tid1, "12345_2024-07-01_10_20")
        self.assertEqual(tid1, tid2)

    def test_prepare_bronze_doc_cleans_nan_and_adds_transfer_id(self):
        raw = {
            "player_id": 467994,
            "transfer_date": "2025-06-30",
            "from_club_id": 5621,
            "to_club_id": 749,
            "transfer_fee": float("nan"),
            "player_name": "  Luca Belardinelli  ",
        }
        doc = bronze_ingest.prepare_bronze_doc(raw, "transfers", source_label="local_backup")
        self.assertIsNotNone(doc)
        self.assertEqual(doc["transfer_id"], "467994_2025-06-30_5621_749")
        self.assertIsNone(doc["transfer_fee"])
        self.assertEqual(doc["player_name"], "Luca Belardinelli")
        self.assertEqual(doc["_source"], "local_backup")
        self.assertIn("_ingested_at", doc)

    def test_prepare_bronze_doc_skips_missing_primary_key(self):
        raw = {
            "appearance_id": None,
            "game_id": 999,
            "player_id": 10,
            "competition_id": "CL",
        }
        doc = bronze_ingest.prepare_bronze_doc(raw, "appearances")
        self.assertIsNone(doc)

    def test_validate_columns_raises_on_missing(self):
        with self.assertRaises(ValueError):
            bronze_ingest.validate_columns(["game_id", "season"], "games")

    def test_downloader_falls_back_to_local_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            backup_file = tmp_path / "clubs.csv"
            backup_file.write_text("club_id,name\n1,Real Madrid\n", encoding="utf-8")

            with patch.object(downloader, "download_dataset", side_effect=ConnectionError("cdn down")):
                path, source = downloader.get_dataset_file("clubs", backup_dir=tmp_path, offline=False)
                self.assertEqual(path, backup_file)
                self.assertEqual(source, "local_backup")

    def test_offline_mode_raises_if_no_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(FileNotFoundError):
                downloader.get_dataset_file("clubs", backup_dir=Path(tmpdir), offline=True)


if __name__ == "__main__":
    unittest.main()
