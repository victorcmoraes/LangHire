import asyncio
from datetime import datetime, timezone

import pytest
from core.application_safety import AttemptStore, SafetyError


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
