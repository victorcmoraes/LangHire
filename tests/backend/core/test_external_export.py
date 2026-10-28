import json
import csv
from core.application_safety import AttemptStore
from core.external_export import export_confirmed


def test_export_preserves_manual_bytes_and_is_idempotent_for_confirmed_only(tmp_path):
    (tmp_path/'external').mkdir()
    job={'url':'https://job-boards.greenhouse.io/fixture/jobs/123','title':'Backend Engineer','company':'Fixture',
         'platform':'greenhouse','location':'Brazil Remote'}
    (tmp_path/'external/catalog.json').write_text(json.dumps({'jobs':[job]}))
    store=AttemptStore(tmp_path);confirmed=store.begin(job['url']);store.transition(confirmed,'confirmed','Fixture receipt')
    uncertain=store.begin(job['url'].replace('123','456'));store.transition(uncertain,'uncertain','No receipt')
    tracker=tmp_path/'tracker.csv';original=b'link,status,observacoes,custom\r\nhttps://manual.invalid,Mapeada,"keep, this",my value'
    tracker.write_bytes(original)
    assert export_confirmed(tmp_path,tracker)['appended']==1
    assert tracker.read_bytes().startswith(original+b'\r\n')
    assert b'456' not in tracker.read_bytes() and b'my value' in tracker.read_bytes()
    once=tracker.read_bytes()
    assert export_confirmed(tmp_path,tracker)['appended']==0 and tracker.read_bytes()==once
    assert list((tmp_path/'backups/exports').glob('*/tracker.csv'))


def test_email_continuation_exports_the_verified_parent_resume(tmp_path):
    (tmp_path/'external').mkdir()
    job={'url':'https://job-boards.greenhouse.io/fixture/jobs/123','title':'Backend Engineer','company':'Fixture','platform':'greenhouse'}
    (tmp_path/'external/catalog.json').write_text(json.dumps({'jobs':[job]}))
    store=AttemptStore(tmp_path);aid=store.begin(job['url']);store.transition(aid,'confirmed','Email continuation receipt')
    directory=tmp_path/'evidence'/aid
    (directory/'parent-external-review.json').write_text(json.dumps({'snapshot':{'answers':[{'value':'configured.pdf','sha256':'verified-hash'}]}}))
    target=tmp_path/'tracker.csv';export_confirmed(tmp_path,target)
    with target.open(newline='',encoding='utf-8') as f:row=next(csv.DictReader(f))
    assert row['versao_curriculo']=='configured.pdf'
    before=target.read_bytes();assert export_confirmed(tmp_path,target)['appended']==0
    assert target.read_bytes()==before
