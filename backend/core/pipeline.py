"""Read-only pipeline projection. Aggregation accepts snapshots and has no side effects."""
from collections import Counter
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
from .application_safety import job_key
from .external_catalog import identity


BUCKETS={
    'outside_tracks':'Outside tracks','outside_seniority_or_specialty':'Outside tracks',
    'mobile_outside_tracks':'Outside tracks','pure_frontend_outside_tracks':'Outside tracks',
    'mandatory_duration_above_confirmed':'Experience above confirmed years',
    **dict.fromkeys(('brazil_excluded_by_posting','explicit_residence_restriction_not_supported',
        'onsite_or_hybrid_outside_home_region','working_arrangement_needs_evidence',
        'geographic_eligibility_needs_evidence'),'Location / hybrid'),
    **dict.fromkeys(('mandatory_language_not_confirmed','mandatory_schedule_not_confirmed'),'Language/schedule'),
    **dict.fromkeys(('affirmative_eligibility_not_confirmed','specialized_requirement_needs_evidence',
        'mandatory_duration_not_confirmed','specific_production_ml_requirement','active_business_entity_not_confirmed',
        'specific_production_agent_requirement','missing_requirements','availability_not_confirmed','not_triaged'),'Needs evidence'),
}


def reason_bucket(reason):return BUCKETS.get(reason,'Other')
def short(value,limit=500):return str(value or '')[:limit]


MANUAL_PLATFORMS=('ashby','cornerstone')  # Collected for a manual queue; no application adapter.


def application_status(attempts,job,questions,paused=()):
    if any(a['state']=='confirmed' for a in attempts):return 'confirmed'
    if any(a['state'] in ('uncertain','submitted') or (a['send_intent'] and a['state'] in ('running','awaiting_human','pending')) for a in attempts):return 'uncertain'
    if attempts:
        last=attempts[-1]
        if last['state']=='pending' and questions:return 'blocked-by-question'
        if 'redirect' in last['message'].lower():return 'redirected-manual'
        return last['state']
    if job.get('status')=='applied':return 'confirmed'
    if job.get('status')=='uncertain':return 'uncertain'
    if questions:return 'blocked-by-question'
    if job.get('external_result')=='redirected' or job.get('status')=='redirected':return 'redirected-manual'
    platform=job.get('platform') or job.get('source')
    if platform in MANUAL_PLATFORMS:return 'manual-only'
    if platform in paused:return 'platform-paused'
    return 'not-attempted'


def build_pipeline(catalog=(),jobs=(),decisions=(),attempts=(),outcomes=(),questions=(),generated_at='',paused=()):
    """One identity, one path; terminals intentionally have no outgoing flow."""
    merged={};aliases={};url_keys={}
    for row in [*jobs,*catalog]:
        if not row.get('url'):continue
        row={**(row.get('legacy') or {}),**row}
        key=job_key(row['url']);ident=identity(row)
        key=aliases.get(ident,key) if ident[2] else key
        if ident[2]:aliases[ident]=key
        url_keys[job_key(row['url'])]=key
        merged[key]={**merged.get(key,{}),**row}
    triage={}
    for row in decisions:
        key=aliases.get(identity(row)) or (job_key(row['url']) if row.get('url') else None)
        if key:
            triage[key]=row
            merged.setdefault(key,row)
    grouped={}
    for row in sorted(attempts,key=lambda a:(a.get('created_at',''),a.get('updated_at',''),a['id'])):
        key=url_keys.get(job_key(row['job_url']),job_key(row['job_url']));merged.setdefault(key,{'url':row['job_url']})
        grouped.setdefault(key,[]).append({k:short(row.get(k)) for k in ('id','state','message','created_at','updated_at')} | {'send_intent':bool(row.get('send_intent'))})
    mailbox={}
    for row in sorted(outcomes,key=lambda r:r.get('reply_at') or r.get('sent_at') or ''):
        if row.get('url'):mailbox[url_keys.get(job_key(row['url']),job_key(row['url']))]=row
    unresolved={url_keys.get(job_key(url),job_key(url)) for url in questions}
    nodes={};links=Counter();result=[]
    def node(id,label,stage):
        nodes[id]={'id':id,'label':label,'stage':stage};return id
    for key,job in sorted(merged.items()):
        trail=grouped.get(key,[]);decision=triage.get(key,job.get('external_qualification') or {})
        status=application_status(trail,job,key in unresolved,paused)
        # Historical sends remain visible even if the qualification snapshot is older.
        state=decision.get('state','pending')
        if state not in ('eligible','pending','discarded','closed','protected'):state='pending'
        if status in ('confirmed','uncertain') or any(a['state'] in ('running','awaiting_human') for a in trail):state='protected'
        reason=short(decision.get('reason') or 'not_triaged');bucket=reason_bucket(reason)
        outcome=mailbox.get(key,{})
        stage=outcome.get('stage','no_reply')
        if stage not in ('assessment','interview','advanced','rejected','received','no_reply','verification','unclassified'):stage='unclassified'
        path=[node('collected','Collected',0)]
        label=state.capitalize()+(': '+bucket if state in ('pending','discarded') else '')
        path.append(node('triage:'+state+(':'+bucket if state in ('pending','discarded') else ''),label,1))
        if state in ('eligible','protected'):
            sent=status in ('confirmed','uncertain')
            app=('sent' if sent else status if status in ('blocked-by-question','platform-paused','manual-only')
                 else 'manual-only' if status=='redirected-manual' else 'not-attempted')
            path.append(node('application:'+app,{'sent':'Sent','blocked-by-question':'Blocked by question','platform-paused':'Platform paused',
                                                 'manual-only':'Manual only','not-attempted':'Not attempted'}[app],2))
            if sent:
                path.append(node('receipt:'+status,status.capitalize(),3))
                if status=='confirmed':
                    company='advanced' if stage in ('assessment','interview','advanced') else 'rejected' if stage=='rejected' else 'no_reply'
                    path.append(node('outcome:'+company,{'advanced':'Advanced','rejected':'Rejected','no_reply':'No reply yet'}[company],4))
        links.update(zip(path,path[1:]))
        result.append({'job_key':key,**{k:short(job.get(k),2000 if k=='url' else 250) for k in ('title','company','board','url','location','collected_at','source')},
            'platform':short('linkedin' if key.startswith('linkedin:') else job.get('platform') or job.get('source') or 'unknown',80),
            'triage':{'state':state,'recorded_state':short(decision.get('state')),'reason':reason,'bucket':bucket,'evidence':[short(e) for e in decision.get('evidence',[])[:3] if isinstance(e,str)]},
            'attempts':trail,'application_status':status,'outcome':{'stage':stage,'reply_at':short(outcome.get('reply_at')),'ambiguous':bool(outcome.get('ambiguous'))},
            'last_update':max([short(job.get('collected_at')),short(job.get('applied_at')),short(outcome.get('reply_at'))]+[a['updated_at'] for a in trail]),'path':path})
    totals={'collected':len(result),'eligible':sum(j['triage']['state']=='eligible' for j in result),
        'sent':sum(j['application_status'] in ('confirmed','uncertain') for j in result),
        'confirmed':sum(j['application_status']=='confirmed' for j in result),
        'advanced':sum(j['application_status']=='confirmed' and j['outcome']['stage'] in ('assessment','interview','advanced') for j in result)}
    return {'generated_at':generated_at,'jobs':result,'sankey':{'nodes':list(nodes.values()),'links':[{'source':s,'target':t,'value':v} for (s,t),v in sorted(links.items())]},'totals':totals}


def read_pipeline(directory):
    """Read snapshots only; never instantiate stores whose constructors create files."""
    root=Path(directory)
    def read(path,default):
        try:return json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:return default
    jobs=read(root/'jobs.json',{})
    jobs=[dict(j,url=url) for url,j in jobs.items()] if isinstance(jobs,dict) else jobs
    catalog=read(root/'external/catalog.json',{}).get('jobs',[])
    decisions=read(root/'external/triage.json',{}).get('decisions',[])
    reports=sorted((root/'reports').glob('outcomes-*.json'))
    outcomes=read(reports[-1],{}).get('applications',[]) if reports else []
    attempts=[];questions=[];path=root/'application_attempts.sqlite'
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('BEGIN')
            db.row_factory=sqlite3.Row
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            sends={r[0] for r in db.execute('SELECT DISTINCT attempt_id FROM submissions')} if 'submissions' in tables else set()
            if 'attempts' in tables:attempts=[dict(r,send_intent=r['id'] in sends) for r in db.execute('SELECT * FROM attempts')]
            if {'easy_answers','easy_answer_jobs'}<=tables:
                questions=[r[0] for r in db.execute("SELECT j.job_url FROM easy_answer_jobs j JOIN easy_answers a ON a.id=j.answer_id WHERE a.state!='approved'")]
    # A platform hold file pauses that platform's sends (see external_apply.platform_hold).
    paused={p.name.split('-',1)[0] for p in (root/'external').glob('*-verification-hold.json')}
    return build_pipeline(catalog,jobs,decisions,attempts,outcomes,questions,datetime.now(timezone.utc).isoformat(),paused)
