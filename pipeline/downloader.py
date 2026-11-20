from pathlib import Path
import requests
from config import BACKUP_DATA_DIR, BRONZE_DATASETS, TRANSFERMARKT_CDN_BASE


def download_dataset(name: str, dest_dir: Path = BACKUP_DATA_DIR) -> Path:
    """Download one transfermarkt .csv.gz file into backup_data."""
    if name not in BRONZE_DATASETS:
        raise ValueError(f"Unknown dataset: {name}")

    filename = BRONZE_DATASETS[name]["filename"]
    url = f"{TRANSFERMARKT_CDN_BASE}/{filename}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    target_path = dest_dir / filename

    print(f"[downloader] fetching {url} ...")
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()

    with open(target_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)

    print(f"[downloader] saved -> {target_path}")
    return target_path


def download_all(dest_dir: Path = BACKUP_DATA_DIR) -> dict:
    paths = {}
    for name in BRONZE_DATASETS:
        paths[name] = download_dataset(name, dest_dir=dest_dir)
    return paths


if __name__ == "__main__":
    download_all()
