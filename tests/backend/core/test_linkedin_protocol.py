"""Anonymized operation identifiers observed in the current native LinkedIn UI."""
import gzip
import json
import pytest
from pathlib import Path
from copy import deepcopy
from core.submission_monitor import linkedin_request_kind, request_metadata

ROOT='https://www.linkedin.com/flagship-web/rsc-action/actions/'


def submit_fixture():
    return json.loads((Path(__file__).resolve().parents[2]/'fixtures/linkedin/rsc-final-submit.json').read_text())


def test_observed_final_submit_is_separate_from_preparation_requests():
    from core.submission_monitor import is_submission,linkedin_submit_job_id
    payload=submit_fixture()
    assert linkedin_request_kind(ROOT+'server-request','POST',json.dumps(payload))=='submit'
    assert is_submission(ROOT+'server-request','POST',json.dumps(payload))
    assert linkedin_submit_job_id(payload)=='123'
    bad=deepcopy(payload);bad['serverRequest']['requestedArguments']['payload']['jobId']='456'
    assert linkedin_request_kind(ROOT+'server-request','POST',json.dumps(bad))=='unsupported'
    bad=deepcopy(payload);bad['requestId']+='Application'
    assert linkedin_request_kind(ROOT+'server-request','POST',json.dumps(bad))=='unsupported'
    bad=deepcopy(payload);bad['requestedArguments']['payload']['unknownMutation']=True
    assert linkedin_request_kind(ROOT+'server-request','POST',json.dumps(bad))=='unsupported'
    bad=deepcopy(payload);del bad['states']
    assert linkedin_request_kind(ROOT+'server-request','POST',json.dumps(bad))=='unsupported'


@pytest.mark.parametrize('name',['aboutTheJob','aboutTheCompanyForJobDetails','peopleWhoCanHelp','howYouFitGuide',
 'jobAlertToggle','jobDetailsCourseRecommendations','manageJobBanner','premiumApplicantInsightsForJobDetails',
 'premiumCompanyInsightsForJobDetails','resumeReview','similarJobs','uploadedDocument'])
def test_observed_component_rendering_is_read_only(name):
    body=json.dumps({'componentId':'com.linkedin.sdui.generated.jobseeker.dsl.impl.'+name,'clientArguments':{}})
    assert linkedin_request_kind(ROOT+'component','POST',body)=='read'


@pytest.mark.parametrize('operation,kind',[('handlePickedFile','upload'),('getAmbryUploadUrl','upload'),('setResumeFormBinding','draft')])
def test_file_preparation_and_binding_are_not_final_submissions(operation,kind):
    body=json.dumps({'requestId':'com.linkedin.sdui.requests.jobseeker.easyapply.'+operation})
    assert linkedin_request_kind(ROOT+'server-request','POST',body)==kind


def test_unknown_or_submit_operation_never_inherits_render_or_upload_permission():
    body=json.dumps({'requestId':'com.linkedin.sdui.requests.jobseeker.easyapply.submitApplication'})
    assert linkedin_request_kind(ROOT+'server-request','POST',body)=='unsupported'
    body=json.dumps({'componentId':'com.linkedin.sdui.generated.jobseeker.dsl.impl.submitApplication'})
    assert linkedin_request_kind(ROOT+'component','POST',body)=='unsupported'


def test_compressed_telemetry_is_not_a_submission_and_metadata_excludes_values():
    body=gzip.compress(json.dumps([{'eventInfo':{'eventName':'FixtureEvent'},'eventBody':{'private':'do-not-log'}}]).encode())
    assert linkedin_request_kind('https://www.linkedin.com/random-telemetry','POST',body)=='telemetry'
    meta=request_metadata(json.dumps({'requestId':'com.linkedin.sdui.requests.jobseeker.easyapply.getAmbryUploadUrl','private':'do-not-log'}))
    assert 'do-not-log' not in json.dumps(meta)


def test_navigation_only_allows_the_observed_modal_screen_and_payload():
    body={'isModal':True,'clientArguments':{'screenId':'com.linkedin.sdui.flagshipnav.jobs.easyapply.EasyApply','payload':{'jobId':'123','companyName':'Fixture'}}}
    assert linkedin_request_kind(ROOT+'navigation','POST',json.dumps(body))=='read'
    body['clientArguments']['payload']['submit']=True
    assert linkedin_request_kind(ROOT+'navigation','POST',json.dumps(body))=='unsupported'
