"""Inspect/acknowledge a human hold without submitting or restarting the agent."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.core.config import get_data_dir
from backend.core.application_safety import AttemptStore, SafetyError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "confirm"), nargs="?", default="list")
    parser.add_argument("attempt_id", nargs="?")
    args = parser.parse_args()
    store = AttemptStore(get_data_dir())
    try:
        if args.action == "confirm":
            if not args.attempt_id:
                parser.error("confirm requires an attempt ID")
            store.acknowledge(args.attempt_id)
            print("Confirmação recebida. O worker vai conferir a mesma tentativa; isto não confirma envio.")
        else:
            print(json.dumps(store.list("awaiting_human"), indent=2, ensure_ascii=False))
    except SafetyError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
