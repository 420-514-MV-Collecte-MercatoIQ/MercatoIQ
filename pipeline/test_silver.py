from pathlib import Path
import sys
import unittest
import pandas as pd

# allow running directly from repo root or pipeline/
sys.path.insert(0, str(Path(__file__).resolve().parent))

import silver_transform


class TestSilverTransform(unittest.TestCase):
    def test_parse_transfer_season(self):
        self.assertEqual(silver_transform.parse_transfer_season("23/24"), 2023)
        self.assertEqual(silver_transform.parse_transfer_season("12/13"), 2012)
        self.assertEqual(silver_transform.parse_transfer_season("99/00"), 1999)
        self.assertEqual(silver_transform.parse_transfer_season("2021"), 2021)
        self.assertEqual(
            silver_transform.parse_transfer_season(None, "2020-08-15"), 2020
        )
        self.assertEqual(
            silver_transform.parse_transfer_season(None, "2021-01-10"), 2020
        )

    def test_build_silver_players_embeds_club_and_sorted_valuations(self):
        players_df = pd.DataFrame(
            [
                {
                    "player_id": 7,
                    "name": "Bukayo Saka",
                    "first_name": "Bukayo",
                    "last_name": "Saka",
                    "date_of_birth": "2001-09-05 00:00:00",
                    "current_club_id": 11,
                    "position": "Attack",
                    "sub_position": "Right Winger",
                    "market_value_in_eur": 140000000,
                    "highest_market_value_in_eur": 140000000,
                }
            ]
        )
        clubs_df = pd.DataFrame(
            [
                {
                    "club_id": 11,
                    "name": "Arsenal FC",
                    "domestic_competition_id": "GB1",
                }
            ]
        )
        valuations_df = pd.DataFrame(
            [
                {
                    "player_id": 7,
                    "date": "2022-06-01",
                    "market_value_in_eur": 65000000,
                    "current_club_id": 11,
                    "player_club_domestic_competition_id": "GB1",
                },
                {
                    "player_id": 7,
                    "date": "2024-06-01",
                    "market_value_in_eur": 140000000,
                    "current_club_id": 11,
                    "player_club_domestic_competition_id": "GB1",
                },
                {
                    "player_id": 7,
                    "date": "2023-06-01",
                    "market_value_in_eur": 120000000,
                    "current_club_id": 11,
                    "player_club_domestic_competition_id": "GB1",
                },
            ]
        )

        res_df = silver_transform.build_silver_players_df(
            players_df, clubs_df, valuations_df, max_valuations=2
        )
        docs = silver_transform.clean_records_for_mongo(res_df)
        self.assertEqual(len(docs), 1)
        doc = docs[0]

        self.assertEqual(
            doc["current_club"],
            {
                "club_id": 11,
                "name": "Arsenal FC",
                "club_name": "Arsenal FC",
                "competition_id": "GB1",
            },
        )
        self.assertEqual(len(doc["market_valuations"]), 2)
        self.assertEqual(doc["market_valuations"][0]["date"], "2024-06-01")
        self.assertEqual(doc["market_valuations"][0]["market_value_in_eur"], 140000000.0)
        self.assertEqual(doc["market_valuations"][1]["date"], "2023-06-01")

    def test_build_silver_appearances_age_at_match_and_per_90(self):
        appearances_df = pd.DataFrame(
            [
                {
                    "appearance_id": "app_1",
                    "game_id": 1001,
                    "player_id": 7,
                    "player_club_id": 11,
                    "competition_id": "CL",
                    "date": "2023-10-03",
                    "player_name": "Bukayo Saka",
                    "goals": 1,
                    "assists": 1,
                    "yellow_cards": 0,
                    "red_cards": 0,
                    "minutes_played": 45,
                },
                {
                    "appearance_id": "app_2",
                    "game_id": 1002,
                    "player_id": 7,
                    "player_club_id": 11,
                    "competition_id": "GB1",
                    "date": "2023-10-08",
                    "player_name": "Bukayo Saka",
                    "goals": 0,
                    "assists": 0,
                    "yellow_cards": 0,
                    "red_cards": 0,
                    "minutes_played": 0,
                },
            ]
        )
        players_meta_df = pd.DataFrame(
            [{"player_id": 7, "date_of_birth": "2001-09-05", "position": "Attack"}]
        )
        games_meta_df = pd.DataFrame(
            [{"game_id": 1001, "season": 2023}]
        )

        res_df = silver_transform.build_silver_appearances_df(
            appearances_df, players_meta_df, games_meta_df
        )
        docs = silver_transform.clean_records_for_mongo(res_df)
        self.assertEqual(len(docs), 2)

        first = docs[0]
        self.assertEqual(first["season"], 2023)
        self.assertAlmostEqual(first["age_at_match"], 22.08, places=2)
        self.assertEqual(first["goals_per_90"], 2.0)
        self.assertEqual(first["assists_per_90"], 2.0)
        self.assertEqual(first["ga_per_90"], 4.0)

        # game 1002 wasn't in games_meta_df, so season should be inferred from 2023-10-08
        second = docs[1]
        self.assertEqual(second["season"], 2023)
        self.assertEqual(second["goals_per_90"], 0.0)
        self.assertEqual(second["assists_per_90"], 0.0)
        self.assertEqual(second["ga_per_90"], 0.0)

    def test_build_silver_transfers_normalizes_fees_and_season(self):
        transfers_df = pd.DataFrame(
            [
                {
                    "transfer_id": "t_old",
                    "player_id": 1,
                    "from_club_id": 10,
                    "to_club_id": 20,
                    "transfer_date": "2010-07-01",
                    "transfer_season": "10/11",
                    "transfer_fee": 5000000,
                    "market_value_in_eur": 4000000,
                },
                {
                    "transfer_id": "t_paid",
                    "player_id": 2,
                    "from_club_id": 10,
                    "to_club_id": 30,
                    "transfer_date": "2023-07-15",
                    "transfer_season": "23/24",
                    "transfer_fee": 50000000,
                    "market_value_in_eur": 40000000,
                },
                {
                    "transfer_id": "t_free",
                    "player_id": 3,
                    "from_club_id": 20,
                    "to_club_id": 30,
                    "transfer_date": "2024-01-20",
                    "transfer_season": None,
                    "transfer_fee": float("nan"),
                    "market_value_in_eur": 10000000,
                },
            ]
        )

        res_df = silver_transform.build_silver_transfers_df(
            transfers_df, min_season=2012
        )
        docs = silver_transform.clean_records_for_mongo(res_df)

        # 2010/11 transfer should be filtered out (< 2012)
        self.assertEqual(len(docs), 2)

        paid, free = docs[0], docs[1]
        self.assertEqual(paid["season"], 2023)
        self.assertFalse(paid["is_free_transfer"])
        self.assertAlmostEqual(paid["fee_to_value_ratio"], 1.25, places=4)

        self.assertEqual(free["season"], 2023)
        self.assertEqual(free["transfer_fee"], 0.0)
        self.assertTrue(free["is_free_transfer"])
        self.assertEqual(free["fee_to_value_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
