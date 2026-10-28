import base64
from datetime import datetime,timezone
import json
import os
import time
import pytest
from core.gmail_mailbox import GmailMailbox,MailError,SCOPES,extract_verification,protect_token
from core.application_safety import AttemptStore


def message(identifier='abc123',code='TEST1234',received=None):
    content=f'<p>Copy and paste this code into the security code field on your application:</p><h1>{code}</h1>'
    headers={'From':'Greenhouse <no-reply@us.greenhouse-mail.io>','To':'candidate@example.invalid',
        'Subject':'Security code for your application to Fixture',
        'Authentication-Results':'mx.google.com; dkim=pass header.i=@us.greenhouse-mail.io header.s=mailgun; spf=pass'}
    return {'id':identifier,'internalDate':str(int((received if received is not None else time.time())*1000)),
        'payload':{'mimeType':'text/html','headers':[{'name':k,'value':v} for k,v in headers.items()],
                   'body':{'data':base64.urlsafe_b64encode(content.encode()).decode()}}}


@pytest.mark.parametrize('change',[None,'sender','recipient','company','old','dkim','template'])
def test_only_current_authenticated_company_email_can_supply_a_code(change):
    current=time.time();mail=message(received=current)
    header={'sender':'From','recipient':'To','company':'Subject','dkim':'Authentication-Results'}.get(change)
    if header:
        for item in mail['payload']['headers']:
            if item['name']==header:item['value']={'sender':'no-reply@evil.invalid','recipient':'other@example.invalid',
                'company':'Security code for your application to Another Company','dkim':'mx.google.com; dkim=fail'}[change]
    if change=='old':mail['internalDate']=str(int((current-700)*1000))
    if change=='template':mail['payload']['body']['data']=base64.urlsafe_b64encode(b'<h1>TEST1234</h1>Do something unrelated').decode()
    found=extract_verification(mail,'candidate@example.invalid',['Fixture'],current,clock=current+1)
    if change is None:
        assert found.code=='TEST1234' and 'TEST1234' not in repr(found)
    else:assert found is None


@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI credential store')
def test_windows_token_store_is_encrypted_and_roundtrips():
    secret=b'fixture-refresh-token-not-real';encrypted=protect_token(secret)
    assert secret not in encrypted and encrypted!=secret
    assert protect_token(encrypted,decrypt=True)==secret


def challenge(tmp_path):
    store=AttemptStore(tmp_path);aid=store.begin('https://job-boards.greenhouse.io/fixture/jobs/123')
    store.intent(aid,'one','https://boards.greenhouse.io/fixture/jobs/123');store.response('one',428)
    store.event(aid,'greenhouse_email_precondition',{'request_key':'one','code':'captcha-failed','recipient_present':True})
    sent=datetime.fromisoformat(store.submissions(aid)[0]['at']).timestamp()
    return store,aid,sent


def test_message_claims_prevent_replay_without_persisting_the_code(tmp_path,monkeypatch):
    store,aid,sent=challenge(tmp_path);box=GmailMailbox(tmp_path);box.session=object();box.account='candidate@example.invalid'
    def get(path,params=None):
        if path=='messages':
            assert 'to:candidate@example.invalid' in params['q'];return {'messages':[{'id':'abc123'}]}
        return message()
    monkeypatch.setattr(box,'_get',get)
    result=box.find_code(aid,['Fixture'],sent);assert result.code=='TEST1234'
    assert box.find_code(aid,['Fixture'],sent) is None
    with store.db() as db:
        assert 'TEST1234' not in ' '.join(r[0] for r in db.execute('SELECT payload FROM events'))


def test_ambiguous_messages_and_final_uncertain_attempts_never_supply_a_code(tmp_path,monkeypatch):
    store,aid,sent=challenge(tmp_path);box=GmailMailbox(tmp_path);box.session=object();box.account='candidate@example.invalid'
    monkeypatch.setattr(box,'_get',lambda path,params=None:{'messages':[{'id':'abc123'},{'id':'def456'}]} if path=='messages' else message(identifier=path.split('/')[-1]))
    with pytest.raises(MailError,match='Multiple matching'):box.find_code(aid,['Fixture'],sent)
    store.transition(aid,'uncertain','Session lost')
    monkeypatch.setattr(box,'_get',lambda *a,**k:pytest.fail('No Gmail read after a finalized uncertain attempt'))
    with pytest.raises(MailError,match='No proven live'):box.find_code(aid,['Fixture'],sent)


def test_transport_only_accepts_known_read_paths(tmp_path):
    box=GmailMailbox(tmp_path)
    for path in ['messages/../profile','messages/abc?token=x','messages/send','settings/filters']:
        with pytest.raises(MailError,match='Unsupported Gmail'):box._get(path)


def test_connection_missing_is_reported_without_retrieving_plugin_credentials(tmp_path):
    box=GmailMailbox(tmp_path)
    assert box.status()['authorized'] is False
    with pytest.raises(MailError,match='Desktop OAuth client'):box.connect()
