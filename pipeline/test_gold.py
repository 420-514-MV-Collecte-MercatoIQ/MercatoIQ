from pathlib import Path
import sys
import unittest
import pandas as pd

# allow running directly from repo root or pipeline/
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gold_club_roi
import gold_scouting_u23
import gold_super_subs
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

    def test_scouting_u23_ml_filters_age_and_scores_undervalued_prospect(self):
        players_rows = []
        app_rows = []
        # 10 synthetic U23 players + 1 over-23 player (should be excluded) + 1 low-minute player
        specs = [
            (1, "Prospect Gem", 20, "Attack", "GB1", 10_000_000.0, 22, 18, 9, 2000),
            (2, "Elite Star", 21, "Attack", "ES1", 90_000_000.0, 18, 12, 8, 2200),
            (3, "Young Wing", 19, "Attack", "FR1", 45_000_000.0, 12, 9, 5, 1900),
            (4, "Sharp Nine", 22, "Attack", "L1", 55_000_000.0, 15, 10, 4, 2100),
            (5, "Playmaker", 21, "Midfield", "ES1", 60_000_000.0, 8, 14, 10, 2300),
            (6, "Engine Eight", 22, "Midfield", "GB1", 40_000_000.0, 5, 6, 4, 2050),
            (7, "Regista", 20, "Midfield", "IT1", 32_000_000.0, 3, 5, 6, 1800),
            (8, "Center Back", 19, "Defender", "ES1", 28_000_000.0, 1, 2, 1, 1950),
            (9, "Fullback", 21, "Defender", "L1", 24_000_000.0, 1, 4, 2, 1700),
            (10, "Rotation Mid", 22, "Midfield", "FR1", 18_000_000.0, 2, 2, 1, 1200),
            (11, "Veteran Excluded", 28, "Attack", "GB1", 50_000_000.0, 20, 10, 4, 2100),
            (12, "Low Mins Excluded", 19, "Attack", "IT1", 15_000_000.0, 1, 1, 0, 180),
        ]
        for pid, name, age, pos, comp, mv, g_tot, a_tot, apps_n, mins_tot in specs:
            players_rows.append(
                {
                    "player_id": pid,
                    "player_name": name,
                    "position": pos,
                    "current_market_value_eur": mv,
                    "current_club": {"club_id": 100 + pid, "club_name": f"Club {pid}", "competition_id": comp},
                }
            )
            per_match_mins = max(1, mins_tot // max(1, apps_n)) if apps_n else mins_tot
            n_entries = max(1, apps_n)
            for idx in range(n_entries):
                app_rows.append(
                    {
                        "player_id": pid,
                        "season": 2024,
                        "competition_id": comp,
                        "club_id": 100 + pid,
                        "age_at_match": age,
                        "goals": g_tot if idx == 0 else 0,
                        "assists": a_tot if idx == 0 else 0,
                        "minutes_played": (
                            mins_tot if n_entries == 1 else (mins_tot - per_match_mins * (n_entries - 1) if idx == 0 else per_match_mins)
                        ),
                    }
                )

        clubs_df = pd.DataFrame(
            [{"club_id": 100 + pid, "name": f"Club {pid}", "domestic_competition_id": comp} for pid, _, _, _, comp, *_ in specs]
        )

        scouting_df, metrics = gold_scouting_u23.build_scouting_u23_df(
            pd.DataFrame(players_rows),
            pd.DataFrame(app_rows),
            clubs_df=clubs_df,
            min_minutes=450,
            max_age=23,
        )
        docs = clean_records_for_mongo(scouting_df)
        returned_ids = {d["player_id"] for d in docs}

        self.assertEqual(len(docs), 10)
        self.assertNotIn(11, returned_ids)
        self.assertNotIn(12, returned_ids)
        self.assertIn(metrics["model"], ("Ridge", "RandomForestRegressor"))
        self.assertIsNotNone(metrics["mae_eur"])

        gem = next(d for d in docs if d["player_id"] == 1)
        self.assertGreater(gem["estimated_value_eur"], gem["market_value_eur"])
        self.assertGreater(gem["undervalue_ratio"], 1.0)
        self.assertEqual(gem["club_name"], "Club 1")

    def test_super_subs_distinguishes_late_subs_from_starters(self):
        appearances_df = pd.DataFrame(
            [
                # 3 late sub appearances (>70th min): 60 total mins, 2G+1A = 3 G+A (4.50/90)
                {"game_id": 1001, "player_id": 50, "player_name": "Jhon Duran", "club_id": 405, "competition_id": "GB1", "season": 2024, "position": "Attack", "goals": 1, "assists": 0, "minutes_played": 20},
                {"game_id": 1002, "player_id": 50, "player_name": "Jhon Duran", "club_id": 405, "competition_id": "GB1", "season": 2024, "position": "Attack", "goals": 1, "assists": 0, "minutes_played": 20},
                {"game_id": 1003, "player_id": 50, "player_name": "Jhon Duran", "club_id": 405, "competition_id": "GB1", "season": 2024, "position": "Attack", "goals": 0, "assists": 1, "minutes_played": 20},
                # 2 starter appearances: 180 total mins, 1G+0A = 0.50/90
                {"game_id": 1004, "player_id": 50, "player_name": "Jhon Duran", "club_id": 405, "competition_id": "GB1", "season": 2024, "position": "Attack", "goals": 1, "assists": 0, "minutes_played": 90},
                {"game_id": 1005, "player_id": 50, "player_name": "Jhon Duran", "club_id": 405, "competition_id": "GB1", "season": 2024, "position": "Attack", "goals": 0, "assists": 0, "minutes_played": 90},
            ]
        )
        game_events_df = pd.DataFrame(
            [
                {"game_id": 1001, "minute": 72, "type": "Substitutions", "player_in_id": 50},
                {"game_id": 1002, "minute": 75, "type": "Substitutions", "player_in_id": 50},
                {"game_id": 1003, "minute": 71, "type": "Substitutions", "player_in_id": 50},
            ]
        )
        clubs_df = pd.DataFrame([{"club_id": 405, "name": "Aston Villa"}])

        res_df = gold_super_subs.build_super_subs_df(
            appearances_df,
            game_events_df=game_events_df,
            clubs_df=clubs_df,
            min_sub_apps=3,
            min_sub_minutes=60,
        )
        docs = clean_records_for_mongo(res_df)
        self.assertEqual(len(docs), 1)

        row = docs[0]
        self.assertEqual(row["player_id"], 50)
        self.assertEqual(row["club_name"], "Aston Villa")
        self.assertEqual(row["sub_appearances"], 3)
        self.assertEqual(row["sub_goals"], 2)
        self.assertEqual(row["sub_assists"], 1)
        self.assertEqual(row["sub_minutes_played"], 60)
        self.assertAlmostEqual(row["sub_goal_contributions_per_90"], 4.5, places=4)
        self.assertEqual(row["starter_appearances"], 2)
        self.assertEqual(row["starter_minutes_played"], 180)
        self.assertAlmostEqual(row["starter_goal_contributions_per_90"], 0.5, places=4)
        self.assertGreater(row["super_sub_score"], 75.0)
        self.assertLessEqual(row["super_sub_score"], 100.0)


if __name__ == "__main__":
    unittest.main()
