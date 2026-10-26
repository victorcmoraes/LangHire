"""Command entrypoint for the local-only fork (data stays in .local/)."""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LANGHIRE_DATA_DIR", str(ROOT / ".local"))
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
from backend.core.config import get_data_dir, load_llm_settings, load_settings
from backend.core.application_safety import AttemptStore


def doctor():
    data = get_data_dir()
    llm = load_llm_settings()
    report = {"data_dir": str(data), "python": sys.version.split()[0],
              "profile": (data / "candidate_profile.json").exists(),
              "resume": Path(load_settings().get("resume_path", "")).is_file(),
              "provider": llm.get("provider"), "model": llm.get("ollama", {}).get("model"),
              "model_approval": "not_validated"}
    approval = data / "model_approval.json"
    if approval.exists():
        report["model_approval"] = json.loads(approval.read_text(encoding="utf-8"))
    try:
        url = llm.get("ollama", {}).get("base_url", "http://127.0.0.1:11434")
        from urllib.parse import urlsplit
        if urlsplit(url).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Only a local Ollama endpoint is permitted")
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=5) as response:
            report["installed_models"] = [item["name"] for item in json.load(response).get("models", [])]
        with urllib.request.urlopen(url.rstrip("/") + "/api/ps", timeout=5) as response:
            report["loaded_models"] = [{"name": item["name"], "gpu_bytes": item.get("size_vram", 0)}
                                       for item in json.load(response).get("models", [])]
    except Exception as error:
        report["ollama_error"] = str(error)
    report["attempt_states"] = {}
    store = AttemptStore(data)
    for attempt in store.list():
        state = attempt["state"]
        report["attempt_states"][state] = report["attempt_states"].get(state, 0) + 1
    with store.db() as db:
        report["historical_records"] = db.execute("SELECT COUNT(DISTINCT attempt_id) FROM events WHERE kind='legacy_import'").fetchone()[0]
        report["new_confirmations"] = db.execute("SELECT COUNT(*) FROM attempts a WHERE state='confirmed' AND NOT EXISTS "
                                                 "(SELECT 1 FROM events e WHERE e.attempt_id=a.id AND kind='legacy_import')").fetchone()[0]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("status")
    commands.add_parser("holds")
    confirm = commands.add_parser("confirm")
    confirm.add_argument("attempt_id")
    apply = commands.add_parser("apply")
    apply.add_argument("--limit", type=int, default=1)
    apply.add_argument("--mode", choices=("easy", "external"), default="external")
    args = parser.parse_args()
    store = AttemptStore(get_data_dir())
    if args.command == "doctor":
        print(json.dumps(doctor(), indent=2, ensure_ascii=False))
    elif args.command == "holds":
        print(json.dumps(store.list("awaiting_human"), indent=2, ensure_ascii=False))
    elif args.command == "confirm":
        store.acknowledge(args.attempt_id)
        print("Intervenção confirmada. O worker vai conferir a mesma tentativa, sem reenviar.")
    elif args.command == "status":
        print(json.dumps(store.list(), indent=2, ensure_ascii=False))
    else:
        settings = load_settings()
        if settings.get("submission_enabled") is not True:
            parser.exit(1, "Envios reais ainda não liberados: falta validar o modelo e o piloto neste fork.\n")
        if load_llm_settings().get("provider") != "ollama":
            parser.exit(1, "Este comando exige Ollama local.\n")
        from cli.apply_jobs import main as apply_main
        import asyncio
        sys.argv = ["apply_jobs", "--workers", "1", "--limit", str(args.limit),
                    "--easy-apply" if args.mode == "easy" else "--no-easy-apply"]
        asyncio.run(apply_main())


if __name__ == "__main__":
    main()
