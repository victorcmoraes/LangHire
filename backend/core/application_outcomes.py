"""First-round outcomes from the read-only mailbox: which sent applications advanced.

Only headers, the matched company and the classified stage are stored; message
bodies are read in memory for classification and never persisted.
"""
from datetime import datetime,timezone
from email.utils import getaddresses
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .application_safety import AttemptStore,job_key,now
from .gmail_mailbox import GmailMailbox,_headers,_mime_text

# Ordered by precedence: a rejection that thanks for applying is a rejection.
STAGES=[
    ('verification',r'security code for your application|codigo de seguranca|verification code|codigo de verificacao|confirm your email|confirme seu e mail'),
    ('rejected',r'unfortunately|infelizmente|not (?:to )?(?:move|moving) forward|decided to (?:move|proceed|continue) with other|other candidates|outros candidatos|'
                r'nao (?:seguiremos|vamos seguir|avancaremos|foi selecionad|foi possivel avancar)|nao seguir com (?:a )?sua candidatura|position has been filled|vaga (?:foi )?preenchida|'
                r'not (?:been )?selected|will not be moving|optamos por (?:seguir|prosseguir) com|encerramos (?:o|este) processo|no longer considering'),
    # Automated screenings every applicant receives (CI&T's Digai questions over WhatsApp, and
    # its "last call" reminders): a step to take, not evidence that a person advanced the application.
    ('screening',r'\bdigai\b|ultima chamada|last call|close to boosting your application|seu proximo desafio esta aqui|queremos saber mais sobre voce'),
    ('assessment',r'\bassessment\b|coding challenge|technical (?:test|challenge)|take.?home|hackerrank|codility|codesignal|testgorilla|'
                  r'teste (?:tecnico|online|de logica)|desafio tecnico|case tecnico|avaliacao tecnica|teste de perfil|'
                  r'perguntas tecnicas|technical questions'),
    ('interview',r'\binterview\b|\bentrevista\b|schedule (?:a|an|your) (?:call|chat|conversation|meeting)|book a time|calendly|'
                 r'would like to (?:invite|speak|talk|chat)|gostariamos de (?:convidar|conversar|agendar)|agendar (?:uma )?(?:conversa|call|bate papo)|'
                 r'disponibilidade para (?:uma )?(?:conversa|call|bate papo)'),
    ('received',r'received your application|thank(?:s| you) for (?:applying|your application|your interest)|application (?:has been|was) (?:received|submitted|sent)|'
                r'your application (?:to|for)|recebemos (?:a )?sua candidatura|agradecemos (?:a )?(?:sua )?candidatura|agradecemos por se candidatar|obrigad[oa] por (?:se candidatar|sua candidatura|seu interesse)|candidatura (?:foi )?(?:recebida|enviada|realizada)|'
                r'inscricao (?:foi )?(?:recebida|realizada|confirmada)'),
]
RANK={'verification':0,'unclassified':0,'received':1,'screening':1.5,'rejected':2,'assessment':3,'interview':4}
ADVANCED=('assessment','interview')
ALERT_SENDERS=r'jobalert|job-alert|jobs-listings|alerts?@|newsletter|digest|marketing'
SEARCH='-in:sent -in:chats (application OR applying OR candidatura OR candidato OR candidate OR vaga OR position OR interview OR entrevista OR processo OR assessment OR teste)'


def plain(text):
    from .easy_answers import normalized
    return normalized(unescape(re.sub(r'<[^>]+>',' ',text)))


def classify(subject,text,sender=''):
    """Stage of one recruiting email from its subject and body; job alerts are never replies."""
    if re.search(ALERT_SENDERS,sender,re.I):return 'unclassified'
    content=plain(subject+' \n '+text)
    for stage,pattern in STAGES:
        if re.search(pattern,content):return stage
    return 'unclassified'


def company_names(job):
    """Names an email may use: the display company and the ATS board slug without suffixes."""
    names=set()
    for value in (job.get('company'),job.get('board')):
        if not value:continue
        compact=re.sub(r'[^a-z0-9]','',str(value).lower())
        names.add(compact);names.add(re.sub(r'(?:\d+|inc|llc|ltda|sa|hq|careers|jobs|group|grupo|br|brasil)+$','',compact))
    return {n for n in names if len(n)>=3}


def platform(job,url):
    host=(urlsplit(url).hostname or '').removeprefix('www.')
    return job.get('platform') or next((p for p in ('linkedin','greenhouse','lever') if p in host),host)


def compact(text):
    return re.sub(r'[^a-z0-9]','',plain(text).replace(' ',''))


def sent_applications(data):
    """Confirmed sends, plus uncertain ones that reached the portal; both can produce replies."""
    store=AttemptStore(data);jobs=json.loads((Path(data)/'jobs.json').read_text(encoding='utf-8')) if (Path(data)/'jobs.json').exists() else {}
    by_key={job_key(url):job for url,job in jobs.items()}
    result=[]
    with store.db() as db:
        rows=[dict(r) for r in db.execute("SELECT * FROM attempts WHERE state IN ('confirmed','uncertain')")]
        for row in rows:
            sends=db.execute('SELECT at FROM submissions WHERE attempt_id=? ORDER BY at',(row['id'],)).fetchall()
            if row['state']=='uncertain' and not sends:continue
            metrics=[json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='answer_metrics'",(row['id'],))]
            job=by_key.get(job_key(row['job_url']),{})
            sent_at=(sends[0]['at'] if sends else None) or job.get('applied_at') or row['updated_at']
            result.append({'attempt_id':row['id'],'url':row['job_url'],'state':row['state'],'sent_at':sent_at,
                'company':job.get('company') or '','title':job.get('title') or '','names':sorted(company_names(job)),
                'platform':platform(job,row['job_url']),
                'autonomous_answers':sum(m.get('autonomous',0) for m in metrics)>0})
    return result


def mentions(name,header,message_text):
    """Short names ("flex", "swile") must appear as a word in the sender or subject, not anywhere in a body."""
    if len(name)<6:return bool(re.search(r'\b'+re.escape(name)+r'\b',plain(header)))
    return name in compact(message_text)


def match(applications,message_text,received_at,header=None):
    """Applications at the sender's company sent before the message; the title disambiguates siblings."""
    header=message_text if header is None else header
    candidates=[a for a in applications if a['sent_at']<=received_at and any(mentions(n,header,message_text) for n in a['names'])]
    if len(candidates)<=1:return candidates,False
    words=lambda a:{w for w in plain(a['title']).split() if len(w)>=4}
    scored=[(len(words(a)&set(plain(message_text).split())),a) for a in candidates]
    best=max(s for s,_ in scored)
    chosen=[a for s,a in scored if s==best]
    return chosen,len(chosen)>1


class OutcomeTracker:
    def __init__(self,data,mailbox=None):
        self.data=Path(data);self.store=AttemptStore(self.data);self.mailbox=mailbox or GmailMailbox(self.data)
        with self.store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS mail_outcomes (
              message_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, stage TEXT NOT NULL,
              subject TEXT NOT NULL, sender TEXT NOT NULL, attempt_ids TEXT NOT NULL,
              ambiguous INTEGER NOT NULL DEFAULT 0, scanned_at TEXT NOT NULL);''')

    def scan(self,max_messages=500,rescan=False):
        """Fetch only messages not seen before (all of them after a rule change); the mailbox is never modified."""
        applications=sent_applications(self.data)
        if not applications:return {'scanned':0,'matched':0}
        if rescan:
            with self.store.db() as db:db.execute('DELETE FROM mail_outcomes')
        earliest=min(datetime.fromisoformat(a['sent_at']).timestamp() for a in applications)
        if self.mailbox.session is None:self.mailbox.open()
        with self.store.db() as db:seen={r[0] for r in db.execute('SELECT message_id FROM mail_outcomes')}
        ids=[];token=None
        while len(ids)<max_messages:
            params={'q':f'after:{int(earliest)-60} '+SEARCH,'maxResults':min(100,max_messages-len(ids))}
            if token:params['pageToken']=token
            page=self.mailbox._get('messages',params)
            ids+=[m['id'] for m in page.get('messages',[]) if m['id'] not in seen]
            token=page.get('nextPageToken')
            if not token:break
        matched=0
        for message_id in ids:
            message=self.mailbox._get('messages/'+message_id,{'format':'full'})
            headers=_headers(message)
            subject=' '.join(headers.get('subject',[]));sender=' '.join(headers.get('from',[]))
            text='\n'.join(_mime_text(message.get('payload',{})))[:20000]
            received=datetime.fromtimestamp(int(message.get('internalDate',0))/1000,timezone.utc).isoformat()
            stage=classify(subject,text,sender)
            owners,ambiguous=match(applications,sender+'\n'+subject+'\n'+text,received,sender+'\n'+subject) if stage!='verification' else ([],False)
            matched+=bool(owners)
            with self.store.db() as db:
                db.execute('INSERT OR IGNORE INTO mail_outcomes VALUES(?,?,?,?,?,?,?,?)',(message_id,received,stage,subject[:300],
                    ','.join(a for _,a in getaddresses([sender]))[:200],json.dumps([a['attempt_id'] for a in owners]),int(ambiguous),now()))
        return {'scanned':len(ids),'matched':matched}

    def report(self):
        applications=sent_applications(self.data)
        with self.store.db() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM mail_outcomes ORDER BY received_at')]
        best={}
        for row in rows:
            for aid in json.loads(row['attempt_ids']):
                if RANK[row['stage']]>=RANK.get(best.get(aid,{}).get('stage','unclassified'),0):best[aid]=row
        items=[]
        for a in applications:
            outcome=best.get(a['attempt_id'])
            items.append(dict({k:a[k] for k in ('attempt_id','url','company','title','platform','state','sent_at','autonomous_answers')},
                stage=outcome['stage'] if outcome else 'no_reply',subject=outcome['subject'] if outcome else '',
                reply_at=outcome['received_at'] if outcome else None,ambiguous=bool(outcome and outcome['ambiguous'])))
        def funnel(group):
            stages=[i['stage'] for i in group]
            return {'sent':len(group),'advanced':sum(s in ADVANCED for s in stages),'rejected':stages.count('rejected'),
                    'received_only':stages.count('received'),'screening':stages.count('screening'),'no_reply':stages.count('no_reply')+stages.count('unclassified'),
                    'advance_rate':round(sum(s in ADVANCED for s in stages)/len(group),3) if group else 0}
        groups=lambda key:{str(v):funnel([i for i in items if i[key]==v]) for v in sorted({i[key] for i in items},key=str)}
        return {'generated_at':now(),'total':funnel(items),'by_platform':groups('platform'),
                'by_autonomous_answers':groups('autonomous_answers'),'applications':sorted(items,key=lambda i:i['sent_at'])}
