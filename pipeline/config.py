import os
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

# Charge le fichier .env a la racine du monorepo
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

# Top 5 ligues europeennes + UEFA Champions League (codes Transfermarkt)
TARGET_COMPETITIONS = {
    "GB1": "Premier League",
    "ES1": "La Liga",
    "IT1": "Serie A",
    "L1": "Bundesliga",
    "FR1": "Ligue 1",
    "CL": "UEFA Champions League",
}

def get_db():
    """Retourne une instance connectee a la base MongoDB MercatoIQ."""
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return client[MONGO_DB_NAME]
