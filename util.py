
import json
from pathlib import Path
import pickle


base_path = Path(__file__).parent
CACHE_DIR = base_path / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def pkl_to_json(f_path: Path):
    with open(f_path, "rb") as f:
        data = pickle.load(f)

    with open(f_path.with_suffix(".json"), "w") as f:
        json.dump(data, f)


if __name__ == "__main__":
    # pkl_to_json(CACHE_DIR / "adverts")
    pkl_to_json(CACHE_DIR / "pages")