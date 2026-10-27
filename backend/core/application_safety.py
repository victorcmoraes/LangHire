"""Durable attempts and human holds. A user acknowledgement is never a receipt."""
import asyncio
import json
import os
import sqlite3
import uuid
import re
from urllib.parse import urlsplit
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ACTIVE = ("running", "awaiting_human", "submitted")
PROTECTED = (*ACTIVE, "uncertain", "confirmed")


def now():
    return datetime.now(timezone.utc).isoformat()


class SafetyError(RuntimeError):
    pass


def job_key(url):
    parsed=urlsplit(url)
    host=(parsed.hostname or '').lower().removeprefix('www.')
    match=re.fullmatch(r'/jobs/view/(\d+)/?',parsed.path)
    if host=='linkedin.com' and match:return 'linkedin:'+match[1]
    return host+parsed.path.rstrip('/').removesuffix('/apply').rstrip('/')


class AttemptStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "application_attempts.sqlite"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, job_url TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    owner_pid INTEGER NOT NULL, evidence_dir TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_attempt
                    ON attempts(job_url) WHERE state IN
                    ('running', 'awaiting_human', 'submitted');
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL,
                    at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS submissions (
                    id INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL,
                    request_key TEXT UNIQUE NOT NULL, at TEXT NOT NULL,
                    local_day TEXT NOT NULL, url TEXT NOT NULL,
                    http_status INTEGER
                );
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def begin(self, job_url):
        attempt_id = uuid.uuid4().hex
        directory = self.directory / "evidence" / attempt_id
        with self.db() as db:
            protected=db.execute("SELECT job_url FROM attempts WHERE state IN ('running','awaiting_human','submitted','uncertain','confirmed')").fetchall()
            if any(job_key(r['job_url'])==job_key(job_url) for r in protected):
                raise SafetyError('Existing job identity requires reconciliation; not resubmitting')
            if db.execute("SELECT 1 FROM attempts WHERE job_url=? AND state IN "
                          "('running','awaiting_human','submitted','uncertain','confirmed')",
                          (job_url,)).fetchone():
                raise SafetyError("Existing attempt requires reconciliation; not resubmitting")
            directory.mkdir(parents=True)
            db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,0,?,?)",
                       (attempt_id, job_url, "running", now(), now(), "", os.getpid(), str(directory)))
        return attempt_id

    def get(self, attempt_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise SafetyError("Unknown attempt")
        return dict(row)

    def list(self, state=None):
        with self.db() as db:
            rows = db.execute("SELECT * FROM attempts " + ("WHERE state=? " if state else "") +
                              "ORDER BY created_at DESC", (state,) if state else ()).fetchall()
        return [dict(row) for row in rows]

    def event(self, attempt_id, kind, payload):
        with self.db() as db:
            db.execute("INSERT INTO events(attempt_id,at,kind,payload) VALUES(?,?,?,?)",
                       (attempt_id, now(), kind, json.dumps(payload, ensure_ascii=False)))

    def transition(self, attempt_id, state, message=""):
        if state not in (*ACTIVE, "uncertain", "confirmed", "rejected", "blocked", "reviewed", "pending"):
            raise SafetyError("Invalid attempt state")
        with self.db() as db:
            row = db.execute("SELECT state FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None or row["state"] not in ACTIVE:
                raise SafetyError("Attempt is already final or unknown")
            db.execute("UPDATE attempts SET state=?,message=?,updated_at=?,acknowledged=0 WHERE id=?",
                       (state, message, now(), attempt_id))
            db.execute("INSERT INTO events(attempt_id,at,kind,payload) VALUES(?,?,?,?)",
                       (attempt_id, now(), state, json.dumps({"message": message})))

    def acknowledge(self, attempt_id):
        with self.db() as db:
            row = db.execute("SELECT state FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None or row["state"] != "awaiting_human":
                raise SafetyError("This attempt has no live human hold")
            db.execute("UPDATE attempts SET acknowledged=1,updated_at=? WHERE id=?", (now(), attempt_id))

    async def wait_for_human(self, attempt_id, message, cancel=None):
        self.transition(attempt_id, "awaiting_human", message)
        print(f"\nCAPTCHA / intervenção: {message}\nTentativa: {attempt_id}\n"
              f"Resolva na janela aberta, SEM clicar novamente em enviar. Depois execute:\n"
              f'& "{Path(__file__).resolve().parents[2] / "langhire.cmd"}" confirm {attempt_id}\n', flush=True)
        while not self.get(attempt_id)["acknowledged"]:
            if cancel and cancel.get("cancel_requested"):
                raise asyncio.CancelledError()
            await asyncio.sleep(0.5)
        self.transition(attempt_id, "running", "Human acknowledged; checking the SAME attempt")

    def submissions(self, attempt_id):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM submissions WHERE attempt_id=?", (attempt_id,))]

    def intent(self, attempt_id, request_key, url, daily_limit=10, at=None):
        instant = at or datetime.now(timezone.utc)
        day = instant.astimezone(ZoneInfo("America/Fortaleza")).date().isoformat()
        with self.db() as db:
            if db.execute("SELECT 1 FROM submissions WHERE request_key=?", (request_key,)).fetchone():
                return
            row = db.execute("SELECT state FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None or row["state"] not in ACTIVE:
                raise SafetyError("Attempt is not active")
            # No automatic second send, including after human acknowledgement.
            if db.execute("SELECT 1 FROM submissions WHERE attempt_id=?", (attempt_id,)).fetchone():
                raise SafetyError("A send was already attempted; reconcile instead of resending")
            count = db.execute("SELECT COUNT(*) FROM submissions WHERE local_day=?", (day,)).fetchone()[0]
            if count >= min(max(daily_limit, 1), 200):
                raise SafetyError("Daily submission limit reached")
            db.execute("INSERT INTO submissions(attempt_id,request_key,at,local_day,url) VALUES(?,?,?,?,?)",
                       (attempt_id, request_key, instant.isoformat(), day, url))
            db.execute("UPDATE attempts SET state='submitted',updated_at=? WHERE id=?", (now(), attempt_id))

    def response(self, request_key, status):
        with self.db() as db:
            db.execute("UPDATE submissions SET http_status=? WHERE request_key=?", (status, request_key))

    def recover(self):
        """Called ONLY under the exclusive worker lease, never on API polling."""
        recovered = []
        for attempt in self.list():
            if attempt["state"] in ACTIVE:
                self.transition(attempt["id"], "uncertain" if self.submissions(attempt["id"]) else "pending", "Process stopped; browser session not assumed valid")
                recovered.append(attempt["job_url"])
        return recovered
