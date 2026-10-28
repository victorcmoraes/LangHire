"""Read-only Gmail OAuth and narrowly bound application verification codes.

Credentials and OTPs are never given to Ollama. OTPs exist only in memory and
the active portal's masked inputs. The transport exposes GET requests only.
"""
import asyncio
import base64
import ctypes
from dataclasses import dataclass,field
from datetime import datetime,timezone
from email.utils import getaddresses
from html import unescape
import json
import os
from pathlib import Path
import re
import time

from filelock import FileLock
from .application_safety import AttemptStore,SafetyError,now

SCOPES=['https://www.googleapis.com/auth/gmail.readonly']
BASE='https://gmail.googleapis.com/gmail/v1/users/me/'
SENDERS={'no-reply@us.greenhouse-mail.io','no-reply@eu.greenhouse-mail.io'}


class MailError(SafetyError):
    pass


def protect_token(content,*,decrypt=False):
    """Use this Windows user's DPAPI; never silently save plaintext tokens."""
    if os.name!='nt':raise MailError('Windows credential protection is required for local Gmail tokens')
    class Blob(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(content)
    source=Blob(len(content),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    entropy_buffer=ctypes.create_string_buffer(b'LangHire Gmail read-only v1')
    entropy=Blob(len(entropy_buffer)-1,ctypes.cast(entropy_buffer,ctypes.POINTER(ctypes.c_ubyte)))
    crypt=ctypes.windll.crypt32;kernel=ctypes.windll.kernel32
    if decrypt:ok=crypt.CryptUnprotectData(ctypes.byref(source),None,ctypes.byref(entropy),None,None,1,ctypes.byref(target))
    else:ok=crypt.CryptProtectData(ctypes.byref(source),None,ctypes.byref(entropy),None,None,1,ctypes.byref(target))
    if not ok:raise MailError('Windows could not protect or unlock the Gmail connection')
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    try:return ctypes.string_at(target.data,target.length)
    finally:kernel.LocalFree(ctypes.cast(target.data,ctypes.c_void_p))


def email_address(value):
    addresses=[address.lower() for _,address in getaddresses([str(value)])]
    if len(addresses)!=1 or not re.fullmatch(r'[a-z0-9._+\-]+@[a-z0-9.\-]+\.[a-z]{2,}',addresses[0]):
        raise MailError('A confirmed application email is required')
    return addresses[0]


def expected_email(data):
    path=Path(data)/'confirmed_facts.json'
    source=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'facts':[]}
    values=[f['value'] for f in source['facts'] if f['id']=='email' and not f.get('conflict')]
    if len(values)!=1:raise MailError('Confirm the candidate email before connecting Gmail')
    return email_address(values[0])


def _headers(message):
    headers={}
    for header in message.get('payload',{}).get('headers',[]):
        headers.setdefault(header['name'].lower(),[]).append(header['value'])
    return headers


def _mime_text(payload,depth=0):
    if depth>10:return []
    result=[]
    encoded=payload.get('body',{}).get('data','')
    if payload.get('mimeType') in ('text/plain','text/html') and encoded and len(encoded)<100000:
        try:result.append(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)).decode('utf-8'))
        except (ValueError,UnicodeError):pass
    for part in payload.get('parts',[])[:30]:result.extend(_mime_text(part,depth+1))
    return result


@dataclass(frozen=True)
class VerificationCode:
    code:str=field(repr=False)
    message_id:str
    received_at:str


def extract_verification(message,recipient,companies,sent_at,*,clock=None):
    """Accept only the observed Greenhouse template, account and challenge window."""
    headers=_headers(message)
    senders=[a.lower() for _,a in getaddresses(headers.get('from',[]))]
    if len(senders)!=1 or senders[0] not in SENDERS:return None
    recipients={a.lower() for _,a in getaddresses(headers.get('to',[])+headers.get('delivered-to',[]))}
    if email_address(recipient) not in recipients:return None
    domain=senders[0].split('@')[1]
    authenticated=any(re.match(r'^\s*mx\.google\.com;',h,re.I)
        and re.search(r'dkim=pass\s+header\.i=@'+re.escape(domain)+r'(?:\s|;|$)',h,re.I)
        for h in headers.get('authentication-results',[]))
    if not authenticated:return None
    subject=' '.join(headers.get('subject',[]))
    match=re.fullmatch(r'Security code for your application to (.+)',subject,re.I)
    compact=lambda value:re.sub(r'[^a-z0-9]','',str(value).lower())
    if not match or compact(match[1]) not in {compact(c) for c in companies if c}:return None
    try:received=int(message['internalDate'])/1000
    except (KeyError,ValueError,TypeError):return None
    current=time.time() if clock is None else clock
    if not sent_at-30<=received<=current+5 or current-received>600:return None
    codes=set()
    for text in _mime_text(message.get('payload',{})):
        if 'Copy and paste this code into the security code field on your application' not in text:continue
        codes.update(re.findall(r'<h1\b[^>]*>\s*([a-zA-Z0-9]{8})\s*</h1>',text,re.I))
        plain=unescape(re.sub(r'<[^>]+>',' ',text))
        codes.update(re.findall(r'security code field on your application:\s*([a-zA-Z0-9]{8})(?=\s|$)',plain,re.I))
    if len(codes)!=1 or not message.get('id'):return None
    return VerificationCode(codes.pop(),message['id'],datetime.fromtimestamp(received,timezone.utc).isoformat())


class GmailMailbox:
    def __init__(self,data):
        self.data=Path(data);self.directory=self.data/'mail';self.token_path=self.directory/'gmail-token.dpapi'
        self.session=None;self.credentials=None;self.account=None

    def status(self):
        path=self.directory/'connection.json'
        info=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        return {'client_configured':(self.directory/'google-client.json').is_file(),
                'authorized':self.token_path.is_file(),'account':info.get('email'),
                'access':'read_only','credential_store':'Windows DPAPI'}

    def _save(self,credentials):
        self.directory.mkdir(parents=True,exist_ok=True)
        temporary=self.token_path.with_suffix('.tmp')
        temporary.write_bytes(protect_token(credentials.to_json().encode('utf-8')));temporary.replace(self.token_path)

    def _get(self,path,params=None):
        if not re.fullmatch(r'profile|messages(?:/[a-fA-F0-9]+)?',path):raise MailError('Unsupported Gmail read operation')
        try:response=self.session.get(BASE+path,params=params,timeout=20,allow_redirects=False)
        except Exception as error:raise MailError('Gmail request failed: '+type(error).__name__) from None
        if response.status_code!=200:raise MailError('Gmail read failed with HTTP '+str(response.status_code))
        try:return response.json()
        except ValueError:raise MailError('Gmail returned an invalid response') from None

    def open(self):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import AuthorizedSession,Request
        if not self.token_path.is_file():raise MailError('Gmail is not authorized locally; run python -m cli.mail connect')
        with FileLock(str(self.directory/'token.lock'),timeout=25):
            try:
                info=json.loads(protect_token(self.token_path.read_bytes(),decrypt=True))
                if set(info.get('scopes',[]))!=set(SCOPES):raise MailError('Reconnect Gmail with read-only permission')
                credentials=Credentials.from_authorized_user_info(info,SCOPES)
                if not credentials.valid:credentials.refresh(Request())
            except MailError:raise
            except Exception as error:raise MailError('Reconnect Gmail: '+type(error).__name__) from None
            self.credentials=credentials;self.session=AuthorizedSession(credentials)
            self.account=email_address(self._get('profile')['emailAddress'])
            if self.account!=expected_email(self.data):
                self.session.close();self.session=None;raise MailError('Connected Gmail differs from the confirmed candidate email')
            self._save(credentials)
        return self

    def connect(self,client_path=None):
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import AuthorizedSession
        path=Path(client_path) if client_path else self.directory/'google-client.json'
        if not path.is_file():raise MailError('Create a Google Desktop OAuth client and place its JSON at '+str(self.directory/'google-client.json'))
        config=json.loads(path.read_text(encoding='utf-8'));installed=config.get('installed') or {}
        if (not re.fullmatch(r'[a-zA-Z0-9._\-]+\.apps\.googleusercontent\.com',installed.get('client_id',''))
                or installed.get('token_uri')!='https://oauth2.googleapis.com/token'
                or installed.get('auth_uri') not in ('https://accounts.google.com/o/oauth2/auth','https://accounts.google.com/o/oauth2/v2/auth')):
            raise MailError('Use an original Google OAuth JSON for a Desktop app')
        flow=InstalledAppFlow.from_client_config(config,SCOPES,autogenerate_code_verifier=True)
        credentials=flow.run_local_server(host='127.0.0.1',bind_addr='127.0.0.1',port=0,timeout_seconds=300,
            authorization_prompt_message='Autorize a leitura do Gmail na janela do navegador.',
            success_message='Gmail autorizado. Pode fechar esta janela.',access_type='offline',prompt='consent',login_hint=expected_email(self.data))
        self.credentials=credentials;self.session=AuthorizedSession(credentials)
        try:
            self.account=email_address(self._get('profile')['emailAddress'])
            if self.account!=expected_email(self.data):raise MailError('Choose the same Gmail used in the candidate profile')
            self.directory.mkdir(parents=True,exist_ok=True)
            target=self.directory/'google-client.json'
            if path.resolve()!=target.resolve():target.write_text(json.dumps(config,indent=2),encoding='utf-8')
            with FileLock(str(self.directory/'token.lock'),timeout=25):self._save(credentials)
            (self.directory/'connection.json').write_text(json.dumps({'email':self.account,'scopes':SCOPES,'connected_at':now()},indent=2),encoding='utf-8')
            return self.status()
        finally:self.close()

    def close(self):
        if self.session:self.session.close();self.session=None

    def find_code(self,attempt_id,companies,sent_at):
        store=AttemptStore(self.data);attempt=store.get(attempt_id);sends=store.submissions(attempt_id)
        with store.db() as db:
            proofs=[json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='greenhouse_email_precondition'",(attempt_id,))]
        if (attempt['state'] not in ('submitted','running','awaiting_human') or len(sends)!=1 or sends[0]['http_status']!=428
                or not any(p.get('request_key')==sends[0]['request_key'] and p.get('code')=='captcha-failed' and p.get('recipient_present') is True for p in proofs)):
            raise MailError('No proven live email verification request for this attempt')
        if self.session is None:self.open()
        query=f'after:{int(sent_at)-30} to:{self.account} (from:no-reply@us.greenhouse-mail.io OR from:no-reply@eu.greenhouse-mail.io) subject:"Security code for your application"'
        messages=self._get('messages',{'q':query,'maxResults':10}).get('messages',[])
        matches=[]
        for message in messages:
            with store.db() as db:
                used=db.execute("SELECT 1 FROM events WHERE kind='gmail_verification_claimed' AND json_extract(payload,'$.message_id')=?",(message['id'],)).fetchone()
            if used:continue
            candidate=extract_verification(self._get('messages/'+message['id'],{'format':'full'}),self.account,companies,sent_at)
            if candidate:matches.append(candidate)
        if len(matches)>1:raise MailError('Multiple matching email codes; human selection required')
        if not matches:return None
        candidate=matches[0]
        with store.db() as db:
            used=db.execute("SELECT 1 FROM events WHERE kind='gmail_verification_claimed' AND json_extract(payload,'$.message_id')=?",(candidate.message_id,)).fetchone()
            if used:return None
            db.execute('INSERT INTO events(attempt_id,at,kind,payload) VALUES(?,?,?,?)',
                (attempt_id,now(),'gmail_verification_claimed',json.dumps({'message_id':candidate.message_id,'received_at':candidate.received_at})))
        return candidate


async def wait_for_code(data,attempt_id,companies,sent_at,cancel=None,timeout=120):
    mailbox=GmailMailbox(data);deadline=time.monotonic()+timeout
    try:
        while time.monotonic()<deadline:
            if cancel and cancel.get('cancel_requested'):raise asyncio.CancelledError()
            code=await asyncio.to_thread(mailbox.find_code,attempt_id,companies,sent_at)
            if code:return code
            await asyncio.sleep(min(5,max(0,deadline-time.monotonic())))
        return None
    finally:mailbox.close()
