"""Recruitee Careers Site API: reviewed multipart payload and one durable send."""
import asyncio
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import uuid
from urllib.error import HTTPError
from urllib.parse import quote,urlsplit
import urllib.request

from .application_safety import SafetyError,now
from .easy_answers import AnswerBook,digest,normalized,valid_value
from .automatic_answers import resolve_answer
from .external_catalog import plain


def schema_fields(offer):
    fields=[{'id':key,'name':key,'label':label,'type':kind,'required':True,'options':[]}
            for key,label,kind in [('name','Full name','text'),('email','Email','email')]]
    for key,label,kind,default in [('phone','Phone','tel','required'),('cv','Resume/CV','file','required'),
                                   ('cover_letter','Cover letter','textarea','off')]:
        option=offer.get('options_'+key,default)
        if option=='off':continue
        fields.append({'id':key,'name':key,'label':label,'type':kind,'required':option=='required','options':[]})
    kinds={'string':'text','text':'textarea','single_choice':'select-one','multi_choice':'checkbox-group',
           'date':'text','salary':'number','number':'number','boolean':'checkbox','legal':'checkbox','file':'file'}
    for question in sorted(offer.get('open_questions') or [],key=lambda q:q.get('position',0)):
        fields.append({'id':'question_'+str(question['id']),'name':'question_'+str(question['id']),
            'question_id':question['id'],'label':plain(question['body']),'kind':question['kind'],
            'type':kinds.get(question['kind'],'unsupported'),'required':bool(question.get('required')),
            'options':[{'text':o['body'],'value':o['body']} for o in question.get('open_question_options',[])]})
    return fields


def answer_parts(field,value,index):
    if 'question_id' not in field:return [('candidate['+field['id']+']',value)]
    prefix=f'candidate[open_question_answers_attributes][{index}]'
    parts=[(prefix+'[open_question_id]',str(field['question_id']))]
    if field['kind']=='multi_choice':parts.extend((prefix+f'[multi_content][{k}]',v) for k,v in enumerate(value))
    elif field['kind'] in ('boolean','legal'):parts.append((prefix+'[flag]','true' if value else 'false'))
    else:parts.append((prefix+('[file]' if field['kind']=='file' else '[content]'),str(value)))
    return parts


def multipart(values,files):
    boundary='langhire-'+uuid.uuid4().hex;chunks=[]
    for name,value in values:
        name=quote(name,safe='')
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode('utf-8'))
    for name,path in files:
        filename=path.name.replace('"','_').replace('\r','_').replace('\n','_')
        mime=mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{quote(name,safe="")}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode('utf-8'))
        chunks.extend([path.read_bytes(),b'\r\n'])
    chunks.append(f'--{boundary}--\r\n'.encode())
    return b''.join(chunks),'multipart/form-data; boundary='+boundary


class RecruiteeAdapter:
    def __init__(self,job,data,attempt,status=None):
        self.job=job;self.data=Path(data);self.attempt=attempt;self.book=AnswerBook(data);self.store=self.book.store
        self.directory=Path(self.store.get(attempt)['evidence_dir']);self.status=status if status is not None else {}
        self.metrics={'rule':0,'cache':0,'pending':0,'model':0,'autonomous':0}

    def stage(self,stage):
        self.status.update(stage=stage,job_url=self.job['url'],attempt_id=self.attempt)
        self.store.event(self.attempt,'external_stage',{'stage':stage})

    def write(self,name,value):
        (self.directory/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

    def hold(self,status):
        reason='Recruitee platform requires token (HTTP '+str(status)+'); X-Careers-Sites-Token required from 2027-02-10'
        path=self.data/'external/recruitee-verification-hold.json';path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'active':True,'reason':reason,'checked_at':now(),'allow_read_only_preparation':True}),encoding='utf-8')
        temporary.replace(path)
        self.store.transition(self.attempt,'blocked',reason)

    async def fetch_offer(self):
        host=urlsplit(self.job['url']).hostname
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.recruitee\.com',host or ''):
            raise SafetyError('Invalid Recruitee company URL')
        self.base='https://'+host
        req=urllib.request.Request(self.base+'/api/offers/',headers={'Accept':'application/json'})
        def fetch():
            with urllib.request.urlopen(req,timeout=40) as response:return json.load(response)['offers']
        try:offers=await asyncio.to_thread(fetch)
        except HTTPError as error:
            if error.code in (401,403):self.hold(error.code);return None
            if error.code==404:offers=[]
            else:raise
        offer=next((o for o in offers if str(o['id'])==str(self.job['externalId'])),None)
        if not offer or offer.get('status')!='published':
            self.store.transition(self.attempt,'blocked','Recruitee posting closed or unpublished; no application sent');return None
        from .external_apply import canonical_ats
        if canonical_ats(offer['careers_url'])!=self.job['url'] or normalized(offer['title'])!=normalized(self.job['title']):
            raise SafetyError('Recruitee job identity changed; refresh qualification')
        if not re.fullmatch(r'[A-Za-z0-9_-]+',offer['slug']) or urlsplit(self.job['url']).path!='/o/'+offer['slug']:
            raise SafetyError('Invalid or changed Recruitee offer slug')
        return offer

    async def run(self,resume,execution='review'):
        self.stage('open_external');offer=await self.fetch_offer()
        if offer is None:return
        resume=Path(resume)
        if not resume.is_file() or resume.suffix.lower()!='.pdf':raise SafetyError('Configured PDF missing')
        resume_hash=hashlib.sha256(resume.read_bytes()).hexdigest()
        values=[];files=[];used=[];pending=[];index=0
        fields=schema_fields(offer);self.stage('fill_external')
        for field in fields:
            if field['id']=='cv':
                files.append(('candidate[cv]',resume))
                used.append({'question':field['label'],'value':resume.name,'refs':['configured_resume'],'source':'rule','sha256':resume_hash})
                continue
            answer=await resolve_answer(self.book,field,self.job,self.metrics);self.metrics[answer['source']]+=1
            value=answer.get('value')
            valid='pending' not in answer and field['type']!='unsupported' and valid_value(field,value)
            if valid and field.get('kind')=='date':
                from datetime import date
                try:date.fromisoformat(str(value))
                except ValueError:valid=False
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(value)):valid=False
            if valid and field['type']=='file':valid=isinstance(value,str) and Path(value).is_file()
            if not valid:
                if field['required']:
                    pending.append({'question':field['label'],'answer_id':answer.get('pending',answer.get('answer_id')),
                        'reason':'approval_required' if answer.get('approval_required') else 'unconfirmed_answer',
                        'proposal_state':answer.get('proposal_state'),'proposal_reason':answer.get('proposal_reason')})
                continue
            # Omit unresolved/empty optional answers, including empty multi-selects.
            if value==[]:continue
            parts=answer_parts(field,value,index)
            if field['type']=='file':
                values.extend(parts[:-1]);files.append((parts[-1][0],Path(value)))
            else:values.extend(parts)
            if 'question_id' in field:index+=1
            used.append({'question':field['label'],'value':Path(value).name if field['type']=='file' else value,
                'refs':answer['refs'],'source':answer['source'],'answer_id':answer.get('answer_id'),
                'approval_value':answer.get('approval_value',value)})
        if pending:
            self.write('pending-answers.json',pending)
            self.store.transition(self.attempt,'pending','Unresolved Recruitee questions; see pending-answers.json');return
        url=self.base+'/api/offers/'+quote(offer['slug'],safe='')+'/candidates?async=true'
        file_evidence=[{'field':name,'filename':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for name,path in files]
        if AnswerBook(self.data).version!=self.book.version or hashlib.sha256(resume.read_bytes()).hexdigest()!=resume_hash:
            raise SafetyError('Candidate facts or configured PDF changed before review')
        snapshot={'job_url':self.job['url'],'title':self.job['title'],'company':self.job['company'],'answers':used,
            'fields':fields,'payload':{'url':url,'values':[{'field':name,'value':value} for name,value in values],'files':file_evidence},
            'resume_hash':resume_hash,'facts_version':self.book.version}
        review={'ready':True,'reason':'','digest':digest(snapshot),'snapshot':snapshot}
        self.stage('review_external');self.write('external-review.json',review)
        self.store.event(self.attempt,'external_review',{'ready':True,'digest':review['digest'],'facts_version':self.book.version})
        if execution=='review':
            self.store.transition(self.attempt,'reviewed','Recruitee HTTP payload independently reviewed; no submit');return
        current=AnswerBook(self.data)
        if current.version!=self.book.version or hashlib.sha256(resume.read_bytes()).hexdigest()!=resume_hash:
            raise SafetyError('Candidate facts or configured PDF changed after review')
        approved={a['id']:a for a in current.list()}
        for answer in used:
            if answer.get('answer_id'):
                row=approved.get(answer['answer_id'],{})
                if row.get('state')!='approved' or row.get('answer')!=answer['approval_value']:raise SafetyError('Approved answer changed after review')
        for (_,path),evidence in zip(files,file_evidence):
            if hashlib.sha256(path.read_bytes()).hexdigest()!=evidence['sha256']:raise SafetyError('Attachment changed after review')
        body,content_type=multipart(values,files)
        if AnswerBook(self.data).version!=self.book.version or hashlib.sha256(resume.read_bytes()).hexdigest()!=resume_hash:
            raise SafetyError('Candidate facts or configured PDF changed while encoding payload')
        if self.status.get('cancel_requested'):raise SafetyError('Execution cancelled before send')
        request=urllib.request.Request(url,data=body,headers={'Content-Type':content_type,'Accept':'application/json'},method='POST')
        self.stage('submit_external');request_key='recruitee:'+self.attempt
        self.store.intent(self.attempt,request_key,url)
        def send():
            try:
                with urllib.request.urlopen(request,timeout=60) as response:
                    status=response.status
                    self.store.response(request_key,status)
                    return status,response.read()
            except HTTPError as error:
                self.store.response(request_key,error.code)
                return error.code,error.read(16000)
        status,raw=await asyncio.to_thread(send)
        if status in (401,403):self.hold(status);return
        if status in (400,422):
            self.write('recruitee-error.json',{'status':status,'body':raw.decode('utf-8',errors='replace')[:8000]})
            self.store.transition(self.attempt,'blocked','Recruitee rejected application before candidate creation (HTTP '+str(status)+'); no automatic retry');return
        if 200<=status<300:
            try:identifier=json.loads(raw)['candidate']['id']
            except (ValueError,KeyError,TypeError):identifier=None
            if isinstance(identifier,(str,int)) and not isinstance(identifier,bool) and identifier:
                self.store.event(self.attempt,'recruitee_candidate',{'candidate_id':identifier})
                self.store.transition(self.attempt,'confirmed','Recruitee candidate creation confirmed');return
        self.store.transition(self.attempt,'uncertain','Recruitee response does not prove candidate creation (HTTP '+str(status)+'); reconcile without resending')
