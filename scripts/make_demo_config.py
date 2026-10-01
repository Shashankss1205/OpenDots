"""Create a reproducible local dashboard configuration without changing the main examples."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make(mode, directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "examples/config.json").read_text())
    config["database"] = str(directory / "state.db")
    config["targets"] = [target for target in config["targets"] if mode == "combined" or target["id"] == mode]
    for target in config["targets"]:
        target["workspace"] = str(ROOT / "examples" / target["workspace"])
    if mode in {"react", "combined"}:
        config["schedules"] = [{"id": "react-scout", "target_id": "react", "type": "timer.scout", "interval_seconds": 3600,
                                "payload": {"repo": "facebook/react", "title": "Automatic accessibility Scout"}}]
    path = directory / "config.json"
    path.write_text(json.dumps(config, indent=2) + "\n")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["kubernetes", "react", "combined"])
    parser.add_argument("directory")
    args = parser.parse_args()
    print(make(args.mode, args.directory))
