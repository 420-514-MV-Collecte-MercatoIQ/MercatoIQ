from pathlib import Path
import sys
import unittest
import pandas as pd

# allow running directly from repo root or pipeline/
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gold_club_roi
import gold_ucl_vs_domestic
from silver_transform import clean_records_for_mongo


class TestGoldLayer(unittest.TestCase):
    def test_club_transfer_roi_calculation(self):
        transfers_df = pd.DataFrame(
            [
                {
                    "player_id": 101,
                    "to_club_id": 11,
                    "to_club_name": "Arsenal FC",
                    "season": 2023,
                    "transfer_fee_eur": 30_000_000.0,
                    "market_value_at_transfer_eur": 45_000_000.0,
                },
                {
                    "player_id": 102,
                    "to_club_id": 11,
                    "to_club_name": "Arsenal FC",
                    "season": 2023,
                    "transfer_fee_eur": 0.0,
                    "market_value_at_transfer_eur": 5_000_000.0,
                },
            ]
        )
        clubs_df = pd.DataFrame(
            [{"club_id": 11, "name": "Arsenal FC", "domestic_competition_id": "GB1"}]
        )
        appearances_df = pd.DataFrame(
            [
                {"club_id": 11, "season": 2023, "goals": 10, "assists": 5},
                {"club_id": 11, "season": 2023, "goals": 8, "assists": 7},
            ]
        )

        res_df = gold_club_roi.build_club_transfer_roi_df(
            transfers_df, clubs_df, appearances_df
        )
        docs = clean_records_for_mongo(res_df)
        self.assertEqual(len(docs), 1)

        row = docs[0]
        self.assertEqual(row["club_id"], 11)
        self.assertEqual(row["competition_id"], "GB1")
        self.assertEqual(row["players_bought"], 2)
        self.assertEqual(row["total_fees_paid_eur"], 30_000_000.0)
        self.assertEqual(row["total_value_gain_eur"], 20_000_000.0)
        self.assertAlmostEqual(row["roi"], 0.6667, places=4)
        self.assertEqual(row["total_ga"], 30)
        self.assertAlmostEqual(row["ga_per_m_eur"], 1.0, places=4)

    def test_club_transfer_roi_zero_fees_handles_none_roi(self):
        transfers_df = pd.DataFrame(
            [
                {
                    "player_id": 201,
                    "to_club_id": 22,
                    "to_club_name": "Free FC",
                    "season": 2023,
                    "transfer_fee_eur": 0.0,
                    "market_value_at_transfer_eur": 4_000_000.0,
                }
            ]
        )

        res_df = gold_club_roi.build_club_transfer_roi_df(transfers_df)
        docs = clean_records_for_mongo(res_df)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["total_fees_paid_eur"], 0.0)
        self.assertIsNone(docs[0]["roi"])
        self.assertIsNone(docs[0]["ga_per_m_eur"])

    def test_ucl_vs_domestic_computes_per_90_and_delta(self):
        appearances_df = pd.DataFrame(
            [
                # Player 7 played both CL and GB1 in 2023
                {
                    "player_id": 7,
                    "player_name": "Bukayo Saka",
                    "club_id": 11,
                    "competition_id": "CL",
                    "season": 2023,
                    "position": "Attack",
                    "goals": 4,
                    "assists": 2,
                    "minutes_played": 360,
                },
                {
                    "player_id": 7,
                    "player_name": "Bukayo Saka",
                    "club_id": 11,
                    "competition_id": "GB1",
                    "season": 2023,
                    "position": "Attack",
                    "goals": 9,
                    "assists": 9,
                    "minutes_played": 1620,
                },
                # Player 99 played only domestic GB1 -> should be excluded
                {
                    "player_id": 99,
                    "player_name": "Domestic Only",
                    "club_id": 11,
                    "competition_id": "GB1",
                    "season": 2023,
                    "position": "Midfield",
                    "goals": 2,
                    "assists": 1,
                    "minutes_played": 900,
                },
            ]
        )
        clubs_df = pd.DataFrame([{"club_id": 11, "name": "Arsenal FC"}])

        res_df = gold_ucl_vs_domestic.build_ucl_vs_domestic_df(
            appearances_df, clubs_df=clubs_df
        )
        docs = clean_records_for_mongo(res_df)
        self.assertEqual(len(docs), 1)

        p7 = docs[0]
        self.assertEqual(p7["player_id"], 7)
        self.assertEqual(p7["club_name"], "Arsenal FC")
        self.assertEqual(p7["domestic_competition_id"], "GB1")
        self.assertAlmostEqual(p7["ucl_goals_per_90"], 1.0, places=4)
        self.assertAlmostEqual(p7["domestic_goals_per_90"], 0.5, places=4)
        self.assertAlmostEqual(p7["goals_per_90_delta"], 0.5, places=4)


if __name__ == "__main__":
    unittest.main()
