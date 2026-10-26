"""Copy a POC snapshot into isolated LangHire data; never modify its source."""
import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.core.application_safety import AttemptStore
from backend.core.config import get_data_dir


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def restore_legacy_counters(snapshot, destination):
    """Idempotently preserve old send budgets; URL aliases must not double count."""
    store = AttemptStore(destination)
    source = sqlite3.connect((Path(snapshot) / "queue.sqlite").as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        rows = source.execute("SELECT attempts.*, jobs.url FROM attempts JOIN jobs ON jobs.id=attempts.job_id").fetchall()
    finally:
        source.close()
    with store.db() as db:
        for row in rows:
            attempt = db.execute("SELECT id FROM attempts WHERE job_url=? ORDER BY created_at LIMIT 1", (row["url"],)).fetchone()
            if attempt:
                db.execute("INSERT OR IGNORE INTO submissions(attempt_id,request_key,at,local_day,url) VALUES(?,?,?,?,?)",
                           (attempt["id"], f"legacy-{row['id']}", row["created"], row["day"], row["url"]))
    return len(rows)


def import_poc(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source and destination must be separate directories")
    destination.mkdir(parents=True, exist_ok=True)
    if any((destination / name).exists() for name in ("jobs.json", "candidate_profile.json", "application_attempts.sqlite")):
        raise ValueError("Destination already has operational data; refusing to overwrite")
    profile = json.loads((source / "profile.json").read_text(encoding="utf-8"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot = destination / "imports" / stamp
    snapshot.mkdir(parents=True)
    for name in ("profile.json", "candidate-answers.json", "config.json"):
        if (source / name).exists():
            shutil.copy2(source / name, snapshot / name)
    tracker = source.parent.parent / "tracking" / "candidaturas.csv"
    if tracker.exists():
        shutil.copy2(tracker, snapshot / "candidaturas.csv")
    # SQLite backup includes committed WAL content without touching the original DB.
    original = sqlite3.connect((source / "queue.sqlite").as_uri() + "?mode=ro", uri=True)
    backup = sqlite3.connect(snapshot / "queue.sqlite")
    try:
        original.backup(backup)
    finally:
        original.close()
        backup.close()
    db = sqlite3.connect(snapshot / "queue.sqlite")
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM jobs").fetchall()
    tried = {row[0] for row in db.execute("SELECT DISTINCT job_id FROM attempts")}
    attempt_count = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    db.close()
    # Facts with scoped validity or conflicts stay in the provenance file/notes.
    facts = {fact["id"]: fact["value"] for fact in profile["facts"]
             if not fact.get("conflict") and not fact.get("scope")}
    def fact(key, default=""):
        return facts.get(key, default)
    candidate = {
        "name": fact("name.full"), "email": fact("email"), "phone": fact("phone"),
        "phone_country_code": fact("phone.countryCode", "+55"), "country": "BR",
        "address": {"city": fact("location.city"), "state": fact("location.state"),
                    "country": "Brazil", "street": "", "zip": ""},
        "work_authorization": "Authorized in Brazil without sponsorship. Other countries: unknown.",
        "visa_sponsorship_needed": False, "willing_to_relocate": None,
        "preferred_work_mode": "remote", "years_of_experience": None,
        "education": {"degree": fact("education.degree"), "school": fact("education.school"),
                      "graduation": fact("education.end")},
        "current_role": "", "target_job_titles": ["Backend Engineer", "Integration Engineer", "Data Engineer"],
        "target_locations": ["Brazil", "Recife", "Olinda"], "languages": fact("languages", []),
        "skills": list(dict.fromkeys(fact("skills", []) + fact("skills.confirmed", []) + fact("skills.commonBackend", []))),
        "salary_expectation": {"min": fact("salary.minimumBrlMonthly", 4000), "max": None,
                               "currency": "BRL", "period": "monthly"},
        "notice_period": "", "nationality": "", "date_of_birth": "", "cover_letter": "",
        "date_format": "DD/MM/YYYY",
        "notes": "Only the facts below are supported. null/empty means UNKNOWN, never zero/no. "
                 "Visa answer applies ONLY to Brazil. A salary floor is not a salary expectation. "
                 "Never infer years of experience from language proficiency. Conflicts block only their own answer.\n"
                 + json.dumps(profile["facts"], ensure_ascii=False),
    }
    write_json(destination / "candidate_profile.json", candidate)
    write_json(destination / "confirmed_facts.json", profile)
    for language, path in profile.get("resumes", {}).items():
        pdf = Path(path)
        if not pdf.is_absolute():
            pdf = source.parent / pdf
        if pdf.exists():
            target = destination / "resumes" / f"resume-{language}.pdf"
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(pdf, target)
    store = AttemptStore(destination)
    jobs = {}
    protected = 0
    for row in rows:
        payload = json.loads(row["payload"])
        legacy_status = row["status"]
        status = "applied" if legacy_status == "sent" else (
            "uncertain" if legacy_status in ("uncertain", "working") or row["id"] in tried else "blocked")
        urls = list(dict.fromkeys([row["url"], *([payload["applyUrl"]] if payload.get("applyUrl") else [])]))
        for url in urls:
            jobs[url] = {
                **payload, "url": url, "status": status, "easy_apply": row["platform"] == "linkedin",
                "source": row["platform"], "legacy_id": row["id"], "legacy_status": legacy_status,
                "historical": True, "description": payload.get("description", ""),
                "legacy_snapshot": str(snapshot), "legacy_evidence_root": str(source / "applications"),
                "error": "Imported for history; qualification review required before new applications" if status == "blocked" else None,
            }
            if status in ("applied", "uncertain"):
                attempt = store.begin(url)
                store.event(attempt, "legacy_import", {"job_id": row["id"], "snapshot": str(snapshot), "historical": True})
                store.transition(attempt, "confirmed" if status == "applied" else "uncertain", "Historical POC import, excluded from new pilot")
                protected += 1
    write_json(destination / "jobs.json", jobs)
    restore_legacy_counters(snapshot, destination)
    result = {"source_jobs": len(rows), "legacy_attempts": attempt_count,
              "protected_urls": protected, "imported_urls": len(jobs), "snapshot": str(snapshot)}
    write_json(destination / "import_manifest.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(import_poc(args.source, args.destination or get_data_dir()), indent=2))


if __name__ == "__main__":
    main()
