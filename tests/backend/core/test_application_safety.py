import asyncio
import json
from datetime import datetime, timezone

import pytest
from core.application_safety import AttemptStore, SafetyError


@pytest.mark.parametrize('mode',['valid','wrong_job','missing_screenshot','missing_mail','explicit_rejection','late_mail','no_http'])
@pytest.mark.parametrize('receipt_source',['gmail_plugin','gmail_local_api'])
def test_saved_receipt_reconciliation_never_resends_or_erases_history(tmp_path,mode,receipt_source):
    import json
    from datetime import timedelta
    store=AttemptStore(tmp_path);url='https://job-boards.greenhouse.io/fixture/jobs/123';aid=store.begin(url)
    store.intent(aid,'one-send','https://boards.greenhouse.io/fixture/jobs/123')
    if mode!='no_http':store.response('one-send',200)
    store.event(aid,'portal_response',{'status':200,'semantic_rejection':mode=='explicit_rejection'})
    store.transition(aid,'uncertain','Response body unavailable')
    directory=tmp_path/'evidence'/aid;directory.mkdir(exist_ok=True)
    (directory/'capture.json').write_text(json.dumps([{'url':url.replace('123','456')+'/confirmation' if mode=='wrong_job' else url+'/confirmation',
        'text':'Thank you for applying. Your application has been received.'}]))
    if mode!='missing_screenshot':(directory/'capture.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    mail={'source':receipt_source,'kind':'application_received','message_id':'fixture','sender':'ats@example.invalid',
          'recipient':'candidate@example.invalid','received_at':(datetime.now(timezone.utc)+timedelta(hours=2 if mode=='late_mail' else 0)).isoformat()}
    if mode!='missing_mail':(directory/'mail.json').write_text(json.dumps(mail))
    if mode=='valid':
        assert store.reconcile_confirmed_receipt(aid,'capture.json','capture.png','mail.json')['state']=='confirmed'
        with store.db() as db:
            assert db.execute("SELECT COUNT(*) FROM events WHERE attempt_id=? AND kind='uncertain'",(aid,)).fetchone()[0]==1
            assert db.execute("SELECT COUNT(*) FROM events WHERE attempt_id=? AND kind='receipt_reconciled'",(aid,)).fetchone()[0]==1
    else:
        with pytest.raises(SafetyError):store.reconcile_confirmed_receipt(aid,'capture.json','capture.png','mail.json')
        assert store.get(aid)['state']=='uncertain'
    assert len(store.submissions(aid))==1
    with pytest.raises(SafetyError):store.begin(url)


@pytest.mark.parametrize('status,proof,finalized',[(200,True,False),(428,False,False),(428,True,True)])
def test_email_continuation_never_reopens_ordinary_or_unproven_attempts(tmp_path,status,proof,finalized):
    store=AttemptStore(tmp_path);aid=store.begin('https://job-boards.greenhouse.io/fixture/jobs/123')
    store.intent(aid,'parent-send','https://boards.greenhouse.io/fixture/jobs/123');store.response('parent-send',status)
    if proof:store.event(aid,'greenhouse_email_precondition',{'request_key':'parent-send','code':'captcha-failed','recipient_present':True})
    if finalized:store.transition(aid,'uncertain','Session lost; never reconstruct the challenge')
    with pytest.raises(SafetyError):store.continue_email_verification(aid)
    assert len(store.list())==1 and len(store.submissions(aid))==1


@pytest.mark.parametrize('proof,receipt,allowed',[(False,False,False),(True,True,False),(True,False,True)])
def test_only_operator_audited_never_forwarded_probe_can_be_reconciled(tmp_path,proof,receipt,allowed):
    store=AttemptStore(tmp_path);aid=store.begin('https://www.linkedin.com/jobs/view/123/')
    store.intent(aid,'probe','https://www.linkedin.com/jobs/view/123/')
    if proof:
        store.event(aid,'supervised_protocol_request_blocked',{'forwarded':False,'attempt':aid})
        store.event(aid,'operator_reconciliation',{'final_requests_forwarded':0,'confirmed':False})
    if receipt:store.event(aid,'portal_response',{'status':200})
    store.transition(aid,'uncertain','Guard blocked final diagnostic')
    if allowed:
        store.reconcile_blocked_probe(aid)
        assert store.get(aid)['state']=='blocked'
        assert len(store.submissions(aid))==1
        assert store.begin('https://www.linkedin.com/jobs/view/123/')
    else:
        with pytest.raises(SafetyError):store.reconcile_blocked_probe(aid)
        assert store.get(aid)['state']=='uncertain'


def test_duplicate_and_uncertain_are_never_new_attempts(tmp_path):
    store = AttemptStore(tmp_path)
    attempt = store.begin("https://jobs.lever.co/acme/one")
    with pytest.raises(SafetyError):
        store.begin("https://jobs.lever.co/acme/one")
    store.transition(attempt, "uncertain", "connection lost")
    with pytest.raises(SafetyError):
        AttemptStore(tmp_path).begin("https://jobs.lever.co/acme/one")


async def test_human_hold_waits_for_explicit_ack_and_is_not_confirmation(tmp_path):
    store = AttemptStore(tmp_path)
    attempt = store.begin("https://jobs.lever.co/acme/one")
    waiter = asyncio.create_task(store.wait_for_human(attempt, "captcha"))
    await asyncio.sleep(0.05)
    assert not waiter.done()
    assert store.get(attempt)["state"] == "awaiting_human"
    assert store.submissions(attempt) == []
    AttemptStore(tmp_path).acknowledge(attempt)
    await waiter
    assert store.get(attempt)["state"] == "running"
    assert store.submissions(attempt) == []


@pytest.mark.parametrize('sent',[False,True])
async def test_unattended_hold_expires_without_ack_or_second_send(tmp_path,sent):
    (tmp_path/'settings.json').write_text(json.dumps({'submission_policy':{'daily_limit':None,'unattended_hold_seconds':1}}))
    store=AttemptStore(tmp_path);url='https://job-boards.greenhouse.io/fixture/jobs/1';aid=store.begin(url)
    if sent:store.intent(aid,'send',url)
    with pytest.raises(SafetyError,match='expired'):await store.wait_for_human(aid,'captcha')
    assert store.get(aid)['state']=='awaiting_human' and not store.get(aid)['acknowledged']
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE attempt_id=? AND kind='human_hold_expired'",(aid,)).fetchone()[0]==1
    assert len(store.submissions(aid))==int(sent)
    with pytest.raises(SafetyError):store.begin(url)


@pytest.mark.parametrize('limit',[0,-5,True,'1'])
async def test_invalid_unattended_hold_fails_closed_before_holding(tmp_path,limit):
    (tmp_path/'settings.json').write_text(json.dumps({'submission_policy':{'unattended_hold_seconds':limit}}))
    store=AttemptStore(tmp_path);aid=store.begin('https://jobs.lever.co/acme/one')
    with pytest.raises(SafetyError,match='positive integer'):await store.wait_for_human(aid,'captcha')
    assert store.get(aid)['state']=='running'


def test_recovery_preserves_confirmed_receipt_and_invalidates_live_challenge(tmp_path):
    store = AttemptStore(tmp_path)
    confirmed = store.begin("https://jobs.lever.co/acme/confirmed")
    held = store.begin("https://jobs.lever.co/acme/held")
    store.transition(confirmed, "confirmed", "receipt captured before crash")
    store.transition(held, "awaiting_human", "captcha")
    assert AttemptStore(tmp_path).recover() == ["https://jobs.lever.co/acme/held"]
    assert store.get(confirmed)["state"] == "confirmed"
    assert store.get(held)["state"] == "pending"
    with pytest.raises(SafetyError):
        store.acknowledge(held)


def test_linkedin_identity_blocks_tracking_urls_and_trailing_slash_duplicates(tmp_path):
    store=AttemptStore(tmp_path)
    aid=store.begin('https://linkedin.com/jobs/view/123?trackingId=old')
    store.transition(aid,'uncertain','Unknown result')
    with pytest.raises(SafetyError):store.begin('https://www.linkedin.com/jobs/view/123/')


def test_limit_shared_between_restarts_and_fortaleza_midnight(tmp_path):
    store = AttemptStore(tmp_path)
    one = store.begin("https://jobs.lever.co/acme/one")
    before = datetime(2026, 10, 27, 2, 59, tzinfo=timezone.utc)
    store.intent(one, "one", "https://jobs.lever.co/acme/one/apply", 1, before)
    two = AttemptStore(tmp_path).begin("https://jobs.lever.co/acme/two")
    with pytest.raises(SafetyError, match="Daily"):
        store.intent(two, "two", "https://jobs.lever.co/acme/two/apply", 1, before)
    after = datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc)
    store.intent(two, "two", "https://jobs.lever.co/acme/two/apply", 1, after)
    assert store.submissions(one)[0]["local_day"] == "2026-10-26"
    assert store.submissions(two)[0]["local_day"] == "2026-10-27"


def test_unlimited_policy_survives_restart_and_preserves_history_and_duplicate_guard(tmp_path):
    (tmp_path/'settings.json').write_text(json.dumps({'submission_policy':{'daily_limit':None}}))
    store=AttemptStore(tmp_path)
    for i in range(205):
        aid=store.begin('https://job-boards.greenhouse.io/fixture/jobs/'+str(i))
        AttemptStore(tmp_path).intent(aid,'send-'+str(i),'https://boards.greenhouse.io/fixture/jobs/'+str(i),daily_limit=1)
        store.transition(aid,'uncertain','No receipt; never retry')
    assert store.daily_limit() is None
    with store.db() as db:assert db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0]==205
    with pytest.raises(SafetyError):store.begin('https://job-boards.greenhouse.io/fixture/jobs/0')
    aid=store.begin('https://job-boards.greenhouse.io/fixture/jobs/206')
    store.intent(aid,'one','https://boards.greenhouse.io/fixture/jobs/206')
    with pytest.raises(SafetyError,match='already attempted'):store.intent(aid,'two','https://boards.greenhouse.io/fixture/jobs/206')


@pytest.mark.parametrize('limit',[0,-1,True,'unlimited'])
def test_invalid_daily_policy_fails_closed(tmp_path,limit):
    (tmp_path/'settings.json').write_text(json.dumps({'submission_policy':{'daily_limit':limit}}))
    store=AttemptStore(tmp_path);aid=store.begin('https://example.invalid/job')
    with pytest.raises(SafetyError):store.intent(aid,'send','https://example.invalid/job')
    assert not store.submissions(aid)


def test_hold_ack_never_allows_a_second_submission(tmp_path):
    store = AttemptStore(tmp_path)
    attempt = store.begin("https://jobs.lever.co/acme/one")
    store.intent(attempt, "first", "https://jobs.lever.co/acme/one/apply")
    store.transition(attempt, "awaiting_human", "captcha")
    store.acknowledge(attempt)
    store.transition(attempt, "running", "checking")
    with pytest.raises(SafetyError, match="already attempted"):
        store.intent(attempt, "second", "https://jobs.lever.co/acme/one/apply")
    assert len(store.submissions(attempt)) == 1
