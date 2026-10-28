import json
import pytest
from google_auth_oauthlib.flow import InstalledAppFlow
from core.gmail_mailbox import GmailMailbox,MailError,SCOPES


def test_desktop_oauth_uses_loopback_pkce_read_only_and_encrypts_refresh_token(tmp_path,monkeypatch):
    (tmp_path/'confirmed_facts.json').write_text(json.dumps({'facts':[{'id':'email','value':'candidate@example.invalid'}]}))
    client=tmp_path/'downloaded-client.json'
    client.write_text(json.dumps({'installed':{'client_id':'fixture.apps.googleusercontent.com','client_secret':'public-desktop-client-secret',
        'auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token'}}))
    observed={}
    class Credentials:
        def to_json(self):return json.dumps({'refresh_token':'FIXTURE-REFRESH-SECRET','scopes':SCOPES})
    class Flow:
        def run_local_server(self,**kwargs):observed.update(kwargs);return Credentials()
    def factory(config,scopes,**kwargs):
        assert scopes==SCOPES and kwargs['autogenerate_code_verifier'] is True;return Flow()
    monkeypatch.setattr(InstalledAppFlow,'from_client_config',factory)
    class Session:
        def __init__(self,*args):pass
        def close(self):pass
        def get(self,url,**kwargs):
            assert url.endswith('/profile') and kwargs['allow_redirects'] is False
            class Response:
                status_code=200
                def json(self):return {'emailAddress':'candidate@example.invalid'}
            return Response()
    monkeypatch.setattr('google.auth.transport.requests.AuthorizedSession',Session)
    monkeypatch.setattr('core.gmail_mailbox.protect_token',lambda content,**kwargs:content[::-1])
    mailbox=GmailMailbox(tmp_path);result=mailbox.connect(client)
    assert result['authorized'] and result['account']=='candidate@example.invalid'
    assert observed['host']==observed['bind_addr']=='127.0.0.1' and observed['port']==0
    assert observed['access_type']=='offline'
    assert b'FIXTURE-REFRESH-SECRET' not in mailbox.token_path.read_bytes()
    assert 'refresh_token' not in (tmp_path/'mail/connection.json').read_text()


def test_wrong_oauth_client_is_rejected_before_browser_opens(tmp_path):
    client=tmp_path/'web-client.json';client.write_text(json.dumps({'web':{'client_id':'other'}}))
    with pytest.raises(MailError,match='Desktop app'):GmailMailbox(tmp_path).connect(client)
