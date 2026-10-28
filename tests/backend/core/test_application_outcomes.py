import base64
import json
from core.application_outcomes import OutcomeTracker,classify,company_names,match
from core.application_safety import AttemptStore


def test_rejections_win_over_thanks_and_advances_are_detected():
    assert classify('Your application to QuintoAndar','Thank you for applying. Unfortunately, we decided to move forward with other candidates.')=='rejected'
    assert classify('Atualização da sua candidatura','Infelizmente não seguiremos com a sua candidatura neste momento.')=='rejected'
    assert classify('Next steps','We would like to invite you to an interview. Please book a time on Calendly.')=='interview'
    assert classify('Desafio técnico','Gostaríamos que você realizasse o teste técnico na HackerRank.')=='assessment'
    assert classify('Thank you for applying to Encora','We have received your application.')=='received'
    assert classify('Security code for your application to RD Station','Copy and paste this code')=='verification'
    assert classify('Weekly newsletter','Top stories this week')=='unclassified'


def test_company_names_strip_board_suffixes():
    assert {'encora','exadel','abinbev'}<=company_names({'company':'encora10'})|company_names({'company':'exadelinc'})|company_names({'company':'AB InBev'})


def test_sibling_applications_are_told_apart_by_title():
    apps=[{'attempt_id':'a','names':['quintoandar'],'sent_at':'2026-10-28T00:00:00+00:00','title':'Senior Software Engineer'},
          {'attempt_id':'b','names':['quintoandar'],'sent_at':'2026-10-28T00:00:00+00:00','title':'Data Engineer Pleno'}]
    owners,ambiguous=match(apps,'QuintoAndar: your Data Engineer application','2026-10-29T00:00:00+00:00')
    assert [o['attempt_id'] for o in owners]==['b'] and not ambiguous
    assert match(apps,'QuintoAndar update','2026-10-27T00:00:00+00:00')==([],False)


class FakeMailbox:
    session=object()
    def __init__(self,messages):self.messages=messages;self.fetched=[]
    def _get(self,path,params=None):
        if path=='messages':return {'messages':[{'id':i} for i in self.messages]}
        message_id=path.split('/')[1];self.fetched.append(message_id);subject,sender,body,at=self.messages[message_id]
        return {'id':message_id,'internalDate':str(at*1000),'payload':{'mimeType':'text/plain','headers':[{'name':'Subject','value':subject},{'name':'From','value':sender}],
                'body':{'data':base64.urlsafe_b64encode(body.encode()).decode()}}}


def test_scan_classifies_once_and_reports_the_funnel(tmp_path):
    store=AttemptStore(tmp_path)
    url='https://job-boards.greenhouse.io/encora10/jobs/1'
    aid=store.begin(url);store.transition(aid,'confirmed','ok')
    (tmp_path/'jobs.json').write_text(json.dumps({url:{'company':'encora10','title':'Backend Engineer','platform':'greenhouse','applied_at':'2026-10-28T02:00:00+00:00'}}))
    mailbox=FakeMailbox({'aa':('Encora interview','Encora <no-reply@us.greenhouse-mail.io>','We would like to invite you to an interview',1793300000),
                         'bb':('Other company','Acme <x@acme.com>','Unfortunately we moved forward',1793300000)})
    tracker=OutcomeTracker(tmp_path,mailbox)
    assert tracker.scan()=={'scanned':2,'matched':1}
    assert tracker.scan()=={'scanned':0,'matched':0} and mailbox.fetched==['aa','bb']
    report=tracker.report()
    assert report['total']=={'sent':1,'advanced':1,'rejected':0,'received_only':0,'no_reply':0,'advance_rate':1.0}
    assert report['applications'][0]['stage']=='interview' and report['by_platform']['greenhouse']['advanced']==1


def test_short_names_and_job_alerts_do_not_create_false_replies():
    apps=[{'attempt_id':'f','names':['flex'],'sent_at':'2026-10-28T00:00:00+00:00','title':'Senior Software Engineer'}]
    assert match(apps,'Loja <promo@loja.com>\nGalaxy 56% OFF\nflex display','2026-10-29T00:00:00+00:00','Loja <promo@loja.com>\nGalaxy 56% OFF')==([],False)
    assert match(apps,'Flex <jobs@flex.com>\nYour application','2026-10-29T00:00:00+00:00','Flex <jobs@flex.com>\nYour application')[0]
    assert classify('Mais 22 vagas em Olinda','entrevista amanhã','donotreply@jobalert.indeed.com')=='unclassified'
    assert classify('Agradecemos a sua candidatura - ABI Growth Group','')=='received'
