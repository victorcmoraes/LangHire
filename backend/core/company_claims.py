"""Years of experience stay consistent within one company.

The same recruiter sees every application a candidate sends to one company, so the
years claimed for a stack must not change between two of its forms. The claim the
company already received most often is reused; a form whose answer would contradict it
gets that claim instead, or waits for a person when its control cannot express it.
"""
import json
import math
import re
from urllib.parse import urlsplit

from .application_safety import now


STACKS={
    'java':r'\bjava\b(?! ?script)','spring':r'\bspring\b','python':r'\bpython\b','docker':r'\bdocker\b',
    'kubernetes':r'\bkubernetes\b|\bk8s\b','dotnet':r'\bnet\b|\bdotnet\b|\bc#','azure':r'\bazure\b','aws':r'\baws\b',
    'gcp':r'\bgcp\b|google cloud','go':r'\bgolang\b|\b(?:in|with|em|com) go\b','ruby':r'\bruby\b|\brails\b',
    'node':r'\bnode\b|\bnodejs\b','react':r'\breact\b','angular':r'\bangular\b','typescript':r'\btypescript\b',
    'javascript':r'\bjavascript\b','sql':r'\bsql\b','postgres':r'\bpostgres','oracle':r'\boracle\b','kafka':r'\bkafka\b',
    'cicd':r'\bci ?cd\b|\bjenkins\b|gitlab ci|github actions','microservices':r'micro ?servic|microsservic',
    'api':r'\bapis?\b|\brest(?:ful)?\b','qa':r'quality assurance|\bqa\b|test automation|automacao de testes',
    'release':r'release management','data':r'data engineer|engenharia de dados|analytics engineer',
}
GENERAL=r'desenvolviment|development|back ?end|software|programa|engineering|engenharia|\bti\b|professional experience|experiencia profissional|work experience|worked in'
YEARS_WORD=r'\b(?:anos?|years?|yrs|how long|quanto tempo|ha quantos|tempo de)\b'
NOT_EXPERIENCE=r'\bage\b|idade|consent|retain|government|governo|official|ultimos|last (?:\w+ )?years|for up to'
YES=r'^(?:yes|sim|true|1)$'
NO=r'^(?:no|nao|false|0)$'


def _norm(text):
    from .easy_answers import normalized
    return normalized(text)


def stack_key(question):
    """The stack a years question is about; 'general' for overall development experience."""
    raw=str(question).lower();q=_norm(question)
    if not re.search(YEARS_WORD,q) or re.search(NOT_EXPERIENCE,q):return None
    found=sorted(k for k,p in STACKS.items() if re.search(p,q) or (k=='dotnet' and re.search(r'c#|\.net',raw)))
    if found:return '+'.join(found)
    return 'general' if re.search(GENERAL,q) else None


def interval(text):
    """Years range a displayed answer states: (low, high), high=inf for open ranges."""
    raw=str(text).lower().replace(',','.');t=_norm(text)
    if re.search(r'\b(?:nenhum\w*|none|no experience|sem experiencia|nunca|never)\b',t):return (0.0,0.0)
    number=r'(\d+(?:\.\d+)?)'
    m=re.search(r'(?:menos de|less than|under|ate|up to|abaixo de)\s*'+number,raw)
    if m:return (0.0,float(m[1]))
    m=re.search(number+r'\s*(?:-|–|a|to|e|and)\s*'+number,raw)
    if m:return (float(m[1]),float(m[2]))
    m=re.search(number+r'\s*\+|(?:mais de|more than|over|acima de)\s*'+number+r'|'+number+r'\s*(?:or more|ou mais)',raw)
    if m:return (float(next(g for g in m.groups() if g)),math.inf)
    m=re.search(number,raw)
    return (float(m[1]),float(m[1])) if m else None


def displayed(field,value):
    """What the form shows for a value: the option text when options exist."""
    if isinstance(value,list):
        if len(value)!=1:return None
        value=value[0]
    options={str(o['value']):o['text'] for o in field.get('options',[])}
    if options:return options.get(str(value))
    if isinstance(value,bool):return 'yes' if value else 'no'
    return str(value)


def claim(field,value):
    """(stack, (low, high)) one answer tells the company, or None when it states no years."""
    key=stack_key(field.get('label',''))
    text=displayed(field,value)
    if not key or text is None:return None
    t=_norm(text)
    threshold=re.search(r'(\d+)\s*\+?\s*(?:anos?|years?)',_norm(field.get('label','')))
    if threshold and (re.fullmatch(YES,t) or re.fullmatch(NO,t)):
        n=float(threshold[1])
        return key,((n,math.inf) if re.fullmatch(YES,t) else (0.0,n))
    span=interval(text)
    return (key,span) if span else None


def overlap(a,b):
    return not (a[1]<b[0] or b[1]<a[0])


def company_of(job):
    name=job.get('company') or ''
    if not name:
        parts=[p for p in urlsplit(job.get('url','')).path.split('/') if p]
        name=parts[0] if parts else ''
    return re.sub(r'[^a-z0-9]','',_norm(name))


def expressed(field,value,target):
    """A value for this control stating the target claim; None when the control cannot."""
    choices=[o['value'] for o in field.get('options',[])] or ([True,False] if isinstance(value,bool) else None)
    if choices is not None:
        fits=[]
        for choice in choices:
            c=claim(field,choice)
            if c and overlap(c[1],target):fits.append((c[1][1]-c[1][0],choice))
        if not fits:return None
        best=min(fits,key=lambda f:f[0])[1]
        return [best] if field.get('type')=='checkbox-group' else best
    # Free text states one number: the lower bound of the claim, unless the claim is "less than n".
    if target[0]==0 and target[1]!=0:return None
    years=str(target[0]).removesuffix('.0');original=str(value)
    replaced=re.sub(r'\d+(?:\.\d+)?',years,original,count=1) if re.search(r'\d',original) else years
    c=claim(field,replaced)
    return replaced if c and overlap(c[1],target) else None


class CompanyClaims:
    def __init__(self,store):
        self.store=store
        with store.db() as db:
            fresh=not db.execute("SELECT name FROM sqlite_master WHERE name='company_claims'").fetchone()
            db.executescript('''CREATE TABLE IF NOT EXISTS company_claims (
              company TEXT NOT NULL, stack TEXT NOT NULL, low REAL NOT NULL, high REAL NOT NULL,
              question TEXT NOT NULL, value TEXT NOT NULL, job_url TEXT NOT NULL, at TEXT NOT NULL,
              PRIMARY KEY(company,stack,job_url,question));''')
        if fresh:self.backfill()

    def backfill(self):
        """Claims already sent: approved answers linked to jobs with a confirmed or portal-reached attempt."""
        data=self.store.directory;path=data/'jobs.json'
        jobs=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        with self.store.db() as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'easy_answers','easy_answer_jobs'}<=tables:return
            sent={r[0]:r[1] for r in db.execute("SELECT job_url,MIN(updated_at) FROM attempts WHERE state IN ('confirmed','uncertain') GROUP BY job_url")}
            rows=db.execute("SELECT a.field,a.answer,j.job_url FROM easy_answers a JOIN easy_answer_jobs j ON j.answer_id=a.id WHERE a.state='approved' AND a.answer IS NOT NULL").fetchall()
        for field,answer,url in rows:
            if url not in sent:continue
            job=dict(jobs.get(url,{}),url=url)
            self.record(json.loads(field),json.loads(answer),job,at=sent[url])

    def record(self,field,value,job,at=None):
        c=claim(field,value);company=company_of(job)
        if not c or not company:return
        with self.store.db() as db:
            db.execute('INSERT OR IGNORE INTO company_claims VALUES(?,?,?,?,?,?,?,?)',(company,c[0],c[1][0],c[1][1],
                       field.get('label',''),json.dumps(value,ensure_ascii=False),job.get('url',''),at or now()))

    def settled(self,company,stack,job_url):
        """The claim this company received most often for a stack, from its other applications."""
        with self.store.db() as db:
            rows=db.execute('SELECT low,high,COUNT(*) n,MIN(at) first FROM company_claims WHERE company=? AND stack=? AND job_url<>? '
                            'GROUP BY low,high ORDER BY n DESC,first',(company,stack,job_url)).fetchall()
        return (rows[0][0],rows[0][1]) if rows else None

    def apply(self,field,job,answer):
        """The answer, adjusted to what the company already heard; pending when it cannot be."""
        if 'value' not in answer:return answer
        c=claim(field,answer['value']);company=company_of(job)
        if not c or not company:return answer
        target=self.settled(company,c[0],job.get('url',''))
        if target is None or overlap(c[1],target):
            self.record(field,answer['value'],job);return answer
        value=expressed(field,answer['value'],target)
        if value is None:
            return {'pending':'company-claim-conflict','source':'pending','proposal_reason':'company_claim_conflict','conflict':{'stack':c[0],'claimed':list(target),'answer':answer['value']}}
        self.record(field,value,job)
        return dict(answer,value=value,source=answer.get('source','')+'+company_claim',company_claim={'stack':c[0],'claimed':list(target),'replaced':answer['value']})
