"""Append verified receipts to a CSV without rewriting user-maintained rows."""
import csv
import io
import json
import shutil
import uuid
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from filelock import FileLock

from .application_safety import AttemptStore, SafetyError, job_key

HEADERS=['data','empresa','cargo','trilha','stack_pedida','palavras_chave_ausentes','versao_curriculo',
         'localizacao','contrato','salario_min','salario_max','fonte','status','proxima_acao',
         'data_proxima_acao','follow_up','contato','link','observacoes']


def export_confirmed(data, tracker):
    data=Path(data);tracker=Path(tracker);store=AttemptStore(data)
    catalog=json.loads((data/'external/catalog.json').read_text(encoding='utf-8'))
    jobs={job_key(j['url']):j for j in catalog['jobs']}
    attempts=[a for a in store.list('confirmed') if job_key(a['job_url']) in jobs]
    tracker.parent.mkdir(parents=True,exist_ok=True)
    with FileLock(str(tracker)+'.lock',timeout=0):
        before=tracker.read_bytes() if tracker.exists() else b''
        text=before.decode('utf-8-sig')
        reader=csv.DictReader(io.StringIO(text,newline='')) if before else None
        headers=reader.fieldnames if reader else HEADERS
        if not headers or not {'link','status','observacoes'}.issubset(headers):
            raise SafetyError('Tracker needs link, status and observacoes columns')
        rows=list(reader) if reader else []
        known={job_key(r['link']) for r in rows if r.get('link') and r.get('status','').casefold() in ('aplicada','enviada','applied','confirmed')}
        added=[]
        output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=headers,extrasaction='ignore',lineterminator='\r\n')
        if not before:writer.writeheader()
        for attempt in reversed(attempts):
            key=job_key(attempt['job_url'])
            if key in known:continue
            job=jobs[key];directory=Path(attempt['evidence_dir'])
            review=json.loads((directory/'external-review.json').read_text(encoding='utf-8')) if (directory/'external-review.json').exists() else {}
            resume=next((a.get('value','') for a in review.get('snapshot',{}).get('answers',[]) if a.get('sha256')), '')
            day=datetime.fromisoformat(attempt['created_at']).astimezone(ZoneInfo('America/Fortaleza')).date().isoformat()
            writer.writerow({'data':day,'empresa':job['company'],'cargo':job['title'],
                'trilha':'Backend / Integrations / Automation / Data','versao_curriculo':resume,
                'localizacao':job.get('location',''),'fonte':job['platform'],'status':'Aplicada',
                'proxima_acao':'Acompanhar retorno','link':job['url'],
                'observacoes':f"[langhire:{attempt['id']}] Envio confirmado; comprovantes: {directory}"})
            known.add(key);added.append(attempt['id'])
        if added or not before:
            if before:
                backup=data/'backups/exports'/uuid.uuid4().hex;backup.mkdir(parents=True)
                shutil.copy2(tracker,backup/tracker.name)
            suffix=output.getvalue().encode('utf-8')
            with tracker.open('ab') as target:
                if before and not before.endswith((b'\n',b'\r')):target.write(b'\r\n')
                target.write(suffix)
        return {'tracker':str(tracker),'appended':len(added),'attempt_ids':added,'existing_bytes_preserved':True}
