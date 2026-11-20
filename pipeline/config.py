import os
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

# load root .env file
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb://admin:mercatoiq_dev_pass@localhost:27017/mercatoiq?authSource=admin",
)
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "mercatoiq")
MIN_SEASON = int(os.getenv("PIPELINE_MIN_SEASON", "2012"))

BACKUP_DATA_DIR = Path(
    os.getenv("PIPELINE_BACKUP_DIR", str(Path(__file__).resolve().parent / "backup_data"))
).resolve()

# public cloudflare r2 cdn from dcaribou/transfermarkt-datasets
TRANSFERMARKT_CDN_BASE = os.getenv(
    "TRANSFERMARKT_CDN_BASE",
    "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data",
)

# Top 5 European leagues + UEFA Champions League (Transfermarkt codes)
TARGET_COMPETITIONS = {
    "GB1": "Premier League",
    "ES1": "La Liga",
    "IT1": "Serie A",
    "L1": "Bundesliga",
    "FR1": "Ligue 1",
    "CL": "UEFA Champions League",
}

# datasets to pull into Mongo bronze layer
BRONZE_DATASETS = {
    "competitions": {
        "filename": "competitions.csv.gz",
        "collection": "bronze_competitions",
        "unique_key": ["competition_id"],
    },
    "clubs": {
        "filename": "clubs.csv.gz",
        "collection": "bronze_clubs",
        "unique_key": ["club_id"],
    },
    "players": {
        "filename": "players.csv.gz",
        "collection": "bronze_players",
        "unique_key": ["player_id"],
    },
    "games": {
        "filename": "games.csv.gz",
        "collection": "bronze_games",
        "unique_key": ["game_id"],
    },
    "appearances": {
        "filename": "appearances.csv.gz",
        "collection": "bronze_appearances",
        "unique_key": ["appearance_id"],
    },
    "game_events": {
        "filename": "game_events.csv.gz",
        "collection": "bronze_game_events",
        "unique_key": ["game_event_id"],
    },
    "player_valuations": {
        "filename": "player_valuations.csv.gz",
        "collection": "bronze_player_valuations",
        "unique_key": ["player_id", "date"],
    },
    "transfers": {
        "filename": "transfers.csv.gz",
        "collection": "bronze_transfers",
        "unique_key": ["transfer_id"],
    },
}


def get_db():
    """Returns a connected MongoDB database handle for MercatoIQ."""
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return client[MONGO_DB_NAME]
