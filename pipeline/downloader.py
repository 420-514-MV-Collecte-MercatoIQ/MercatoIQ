from pathlib import Path
import requests
from config import BACKUP_DATA_DIR, BRONZE_DATASETS, TRANSFERMARKT_CDN_BASE


def find_local_backup(name: str, backup_dir: Path = BACKUP_DATA_DIR):
    """Check if we have a local .csv.gz or plain .csv in backup_data."""
    gz_name = BRONZE_DATASETS[name]["filename"]
    gz_path = backup_dir / gz_name
    if gz_path.exists() and gz_path.stat().st_size > 0:
        return gz_path

    # also support plain .csv in case someone copied raw kaggle files manually
    csv_path = backup_dir / gz_name.replace(".csv.gz", ".csv")
    if csv_path.exists() and csv_path.stat().st_size > 0:
        return csv_path

    return None


def download_dataset(name: str, dest_dir: Path = BACKUP_DATA_DIR, timeout: int = 60) -> Path:
    """Download one transfermarkt .csv.gz file safely using a .tmp file first."""
    if name not in BRONZE_DATASETS:
        raise ValueError(f"Unknown dataset: {name}")

    filename = BRONZE_DATASETS[name]["filename"]
    url = f"{TRANSFERMARKT_CDN_BASE}/{filename}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    target_path = dest_dir / filename
    tmp_path = dest_dir / f"{filename}.tmp"

    print(f"[downloader] fetching {url} ...")
    try:
        resp = requests.get(url, stream=True, timeout=timeout)
        resp.raise_for_status()

        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)

        # replace real backup only once download actually finished
        tmp_path.replace(target_path)
        size_mb = target_path.stat().st_size / (1024 * 1024)
        print(f"[downloader] saved {filename} ({size_mb:.2f} MB)")
        return target_path
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def get_dataset_file(name: str, backup_dir: Path = BACKUP_DATA_DIR, offline: bool = False):
    """
    Try to download fresh data from CDN, and automatically fall back
    to pipeline/backup_data/ if the network/CDN is unavailable.
    Returns (file_path, source_label).
    """
    if name not in BRONZE_DATASETS:
        raise ValueError(f"Unknown dataset: {name}")

    if offline:
        local = find_local_backup(name, backup_dir)
        if not local:
            raise FileNotFoundError(
                f"Offline mode enabled but no local backup found for '{name}' in {backup_dir}"
            )
        print(f"[downloader] offline mode -> using {local.name}")
        return local, "local_backup"

    try:
        path = download_dataset(name, dest_dir=backup_dir)
        return path, "remote"
    except Exception as err:
        print(f"[downloader] warn: failed to fetch '{name}' from CDN ({err})")
        local = find_local_backup(name, backup_dir)
        if local:
            print(f"[downloader] failover -> using local backup {local.name}")
            return local, "local_backup"
        raise FileNotFoundError(
            f"CDN failed for '{name}' and no fallback file exists in {backup_dir}"
        ) from err


def download_all(dest_dir: Path = BACKUP_DATA_DIR, offline: bool = False) -> dict:
    results = {}
    for name in BRONZE_DATASETS:
        path, source = get_dataset_file(name, backup_dir=dest_dir, offline=offline)
        results[name] = {"path": path, "source": source}
    return results


if __name__ == "__main__":
    download_all()
