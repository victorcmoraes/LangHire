import json
import sqlite3

import pytest
from cli.import_poc import import_poc
from core.application_safety import AttemptStore


def test_poc_import_preserves_attempts_and_protects_aliases(tmp_path):
    source = tmp_path / "poc"
    source.mkdir()
    (source / "profile.json").write_text(json.dumps({"facts": [
        {"id": "name.full", "value": "Test Candidate", "source": "user"},
        {"id": "experience.start", "value": "2024-01-01", "source": "document"},
        {"id": "visa.sponsorship", "value": False, "scope": "Brazil", "source": "user"},
    ]}), encoding="utf-8")
    with sqlite3.connect(source / "queue.sqlite") as db:
        db.executescript("CREATE TABLE jobs(id INTEGER, url TEXT, platform TEXT, status TEXT, payload TEXT);"
                         "CREATE TABLE attempts(id INTEGER, job_id INTEGER, day TEXT, created TEXT);")
        db.execute("INSERT INTO jobs VALUES(1,?,?,?,?)", (
            "https://www.linkedin.com/jobs/view/1", "lever", "uncertain",
            json.dumps({"title": "Engineer", "applyUrl": "https://jobs.lever.co/acme/one"})))
        db.execute("INSERT INTO attempts VALUES(1,1,'2026-10-26','2026-10-26T12:00:00Z')")
    source_bytes = (source / "queue.sqlite").read_bytes()
    result = import_poc(source, tmp_path / "destination")
    assert result["legacy_attempts"] == 1
    assert result["protected_urls"] == 2
    assert source_bytes == (source / "queue.sqlite").read_bytes()
    candidate = json.loads((tmp_path / "destination/candidate_profile.json").read_text(encoding="utf-8"))
    assert candidate["years_of_experience"] is None
    assert "ONLY to Brazil" in candidate["notes"]
    store = AttemptStore(tmp_path / "destination")
    assert all(row["state"] == "uncertain" for row in store.list())
    assert sum(len(store.submissions(row["id"])) for row in store.list()) == 1
    with pytest.raises(ValueError, match="overwrite"):
        import_poc(source, tmp_path / "destination")
