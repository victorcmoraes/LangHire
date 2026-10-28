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
    match=re.fullmatch(r'/([\w-]+)/jobs/(\d+)(?:/confirmation)?/?',parsed.path)
    if host in ('boards.greenhouse.io','job-boards.greenhouse.io') and match:
        return 'greenhouse:'+match[1]+':'+match[2]
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

    def reconcile_confirmed_receipt(self, attempt_id, capture_name, screenshot_name, mail_name):
        """Reconcile a false negative from retained portal AND mailbox evidence.

        This never authorizes another click or removes the original uncertainty,
        send intention, response, or daily charge from the event history.
        """
        from .submission_monitor import SUCCESS, ERROR, confirmation_matches_job
        import hashlib
        attempt=self.get(attempt_id);directory=Path(attempt['evidence_dir']).resolve()
        files=[]
        for name in (capture_name,screenshot_name,mail_name):
            path=(directory/name).resolve()
            if not path.is_relative_to(directory) or not path.is_file():raise SafetyError('Receipt evidence missing or outside attempt')
            files.append(path)
        captures=json.loads(files[0].read_text(encoding='utf-8'))
        if not isinstance(captures,list) or not any(
                confirmation_matches_job(attempt['job_url'],view.get('url','')) and SUCCESS.search(view.get('text',''))
                and not ERROR.search(view.get('text','')) for view in captures):
            raise SafetyError('No explicit same-job portal receipt')
        if files[1].read_bytes()[:8]!=b'\x89PNG\r\n\x1a\n':raise SafetyError('Receipt screenshot missing')
        mail=json.loads(files[2].read_text(encoding='utf-8'))
        if (mail.get('source') not in ('gmail_plugin','gmail_local_api') or mail.get('kind')!='application_received'
                or not mail.get('message_id') or not mail.get('sender') or not mail.get('recipient')):
            raise SafetyError('Mailbox receipt evidence missing')
        try:received=datetime.fromisoformat(mail['received_at'])
        except (KeyError,ValueError):raise SafetyError('Mailbox receipt date missing')
        with self.db() as db:
            row=db.execute('SELECT state FROM attempts WHERE id=?',(attempt_id,)).fetchone()
            sends=db.execute('SELECT * FROM submissions WHERE attempt_id=?',(attempt_id,)).fetchall()
            responses=[json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='portal_response'",(attempt_id,))]
            if (not row or row['state']!='uncertain' or len(sends)!=1
                    or not sends[0]['http_status'] or not 200<=sends[0]['http_status']<300
                    or any(r.get('semantic_rejection') is True for r in responses)):
                raise SafetyError('Submission is not eligible for receipt reconciliation')
            delta=(received-datetime.fromisoformat(sends[0]['at'])).total_seconds()
            if not -60<=delta<=1800:raise SafetyError('Mailbox receipt does not match the submission window')
            message='Reconciled from saved same-job portal receipt, accepted HTTP response and mailbox receipt; no resend'
            proof={'evidence':[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
                   'mail_message_id':mail['message_id'],'previous_state':'uncertain','resubmitted':False}
            db.execute("UPDATE attempts SET state='confirmed',message=?,updated_at=?,acknowledged=0 WHERE id=?",(message,now(),attempt_id))
            db.execute('INSERT INTO events(attempt_id,at,kind,payload) VALUES(?,?,?,?)',
                       (attempt_id,now(),'receipt_reconciled',json.dumps(proof)))
            db.execute('INSERT INTO events(attempt_id,at,kind,payload) VALUES(?,?,?,?)',
                       (attempt_id,now(),'confirmed',json.dumps({'message':message})))
        return self.get(attempt_id)

    def reconcile_blocked_probe(self, attempt_id):
        """Operator-only recovery for a diagnosed final call proven not forwarded.

        Ordinary uncertain submissions have no such evidence and stay protected.
        The original intent and its daily charge are never deleted.
        """
        with self.db() as db:
            row=db.execute('SELECT state FROM attempts WHERE id=?',(attempt_id,)).fetchone()
            probes=[json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='supervised_protocol_request_blocked'",(attempt_id,))]
            audits=[json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='operator_reconciliation'",(attempt_id,))]
            sends=db.execute('SELECT http_status FROM submissions WHERE attempt_id=?',(attempt_id,)).fetchall()
            receipts=db.execute("SELECT 1 FROM events WHERE attempt_id=? AND kind IN ('portal_response','confirmed')",(attempt_id,)).fetchone()
            if (not row or row['state']!='uncertain' or len(probes)!=1 or probes[0].get('forwarded') is not False
                    or probes[0].get('attempt')!=attempt_id or len(sends)!=1 or sends[0][0] is not None or receipts
                    or not any(a.get('final_requests_forwarded')==0 and a.get('confirmed') is False for a in audits)):
                raise SafetyError('No operator-verified proof that the diagnostic final request was never forwarded')
            message='Operator reconciled diagnostic request blocked before forwarding; original intent remains counted'
            db.execute("UPDATE attempts SET state='blocked',message=?,updated_at=? WHERE id=?",(message,now(),attempt_id))
            db.execute('INSERT INTO events(attempt_id,at,kind,payload) VALUES(?,?,?,?)',
                       (attempt_id,now(),'probe_reconciled',json.dumps({'message':message})))

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

    def daily_limit(self, fallback=10):
        path=self.directory/'settings.json'
        try:
            settings=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            limit=settings.get('submission_policy',{}).get('daily_limit',fallback)
        except (OSError,ValueError,TypeError,AttributeError):
            raise SafetyError('Invalid submission policy; no send permitted') from None
        if limit is not None and (type(limit) is not int or limit<1):
            raise SafetyError('Daily limit must be a positive integer or null for unlimited')
        return limit

    def intent(self, attempt_id, request_key, url, daily_limit=10, at=None):
        daily_limit=self.daily_limit(daily_limit)
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
            if daily_limit is not None and count >= daily_limit:
                raise SafetyError("Daily submission limit reached")
            db.execute("INSERT INTO submissions(attempt_id,request_key,at,local_day,url) VALUES(?,?,?,?,?)",
                       (attempt_id, request_key, instant.isoformat(), day, url))
            db.execute("UPDATE attempts SET state='submitted',updated_at=? WHERE id=?", (now(), attempt_id))

    def response(self, request_key, status):
        with self.db() as db:
            db.execute("UPDATE submissions SET http_status=? WHERE request_key=?", (status, request_key))

    def continue_email_verification(self, attempt_id):
        """A new counted attempt only after GH explicitly requires email verification.

        Ordinary uncertain attempts and CAPTCHA failures cannot use this path.
        The original HTTP 428 and its daily charge are preserved.
        """
        child=uuid.uuid4().hex;directory=self.directory/'evidence'/child
        with self.db() as db:
            parent=db.execute('SELECT * FROM attempts WHERE id=?',(attempt_id,)).fetchone()
            sends=db.execute('SELECT * FROM submissions WHERE attempt_id=?',(attempt_id,)).fetchall()
            proofs=[json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='greenhouse_email_precondition'",(attempt_id,))]
            parsed=urlsplit(parent['job_url']) if parent else None
            if (not parent or parent['state'] not in ACTIVE or not parsed
                    or parsed.hostname not in ('boards.greenhouse.io','job-boards.greenhouse.io')
                    or not re.fullmatch(r'/[\w-]+/jobs/\d+/?',parsed.path)
                    or len(sends)!=1 or sends[0]['http_status']!=428
                    or not any(p.get('request_key')==sends[0]['request_key'] and p.get('code')=='captcha-failed' and p.get('recipient_present') is True for p in proofs)):
                raise SafetyError('No explicit live Greenhouse email precondition; do not repeat the send')
            protected=db.execute("SELECT job_url FROM attempts WHERE id!=? AND state IN ('running','awaiting_human','submitted','uncertain','confirmed')",(attempt_id,)).fetchall()
            if any(job_key(row['job_url'])==job_key(parent['job_url']) for row in protected):
                raise SafetyError('Another protected attempt exists for this job')
            directory.mkdir(parents=True)
            db.execute("UPDATE attempts SET state='blocked',message=?,updated_at=? WHERE id=?",
                       ('Portal explicitly required email verification before accepting application; continued in '+child,now(),attempt_id))
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,0,?,?)',
                       (child,parent['job_url'],'running',now(),now(),'Same live form; email-verification continuation',os.getpid(),str(directory)))
            for aid,kind,payload in ((attempt_id,'verification_continued',{'child_attempt':child}),
                                     (child,'verification_parent',{'parent_attempt':attempt_id,'prior_http_status':428})):
                db.execute('INSERT INTO events(attempt_id,at,kind,payload) VALUES(?,?,?,?)',(aid,now(),kind,json.dumps(payload)))
            for row in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='external_batch'",(attempt_id,)).fetchall():
                batch=json.loads(row[0])
                db.execute('INSERT INTO events(attempt_id,at,kind,payload) VALUES(?,?,?,?)',
                    (child,now(),'external_batch',json.dumps({'batch_id':batch['batch_id'],'attempt_id':child})))
        return child

    def recover(self):
        """Called ONLY under the exclusive worker lease, never on API polling."""
        recovered = []
        for attempt in self.list():
            if attempt["state"] in ACTIVE:
                self.transition(attempt["id"], "uncertain" if self.submissions(attempt["id"]) else "pending", "Process stopped; browser session not assumed valid")
                recovered.append(attempt["job_url"])
        return recovered
