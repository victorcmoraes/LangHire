"""Auditable triage of the imported catalog; no browser/model can assert eligibility."""
from collections import Counter
import json
from pathlib import Path
import re
from .application_safety import AttemptStore,job_key
from .easy_answers import AnswerBook,normalized
from .external_catalog import identity

TRACK=re.compile(r'back.?end|software (?:engineer|developer)|desenvolv|developer|data engineer|engenheir.*dados|analytics engineer|(?:ai|ia|ml|tooling|integration|gtm) engineer|automa[ct]|integrac',re.I)
EXCLUDED=re.compile(r'\b(?:staff|principal|manager|diretor|director|head|architect|arquiteto|master(?!\s+data)|lead|leader|qa|quality|tester|ios|android|mobile|abap)\b',re.I)


def required_text(description):
    lines=description.splitlines();start=None;end=len(lines)
    for i,line in enumerate(lines):
        n=normalized(line)
        if start is None and len(n)<110 and re.match(r'^(requirements|requisitos|qualifications|work experience|must have experience|required skills|what you need|who you are|o que esperamos|o que buscamos|what you bring|what we are looking for|what were looking for|requisitos para este|competencias requeridas)\b',n):start=i
        elif start is not None and re.match(r'^(benefits|beneficios|(?:opcional )?diferenciais|desired|nice to have|bonus|preferred skills|what we offer|english level|legal|o que aumenta|o que oferecemos|you may stand out)\b',n):end=i;break
    return '\n'.join(lines[start:end]) if start is not None else description


def qualify(job,facts):
    title=job['title'];description=job.get('description','');required=required_text(description)
    def result(state,reason,evidence):
        return {'state':state,'reason':reason,'evidence':evidence,'fact_refs':['location.country','location.city','location.state','skills','skills.confirmed','skills.commonExperienceAuthority','experience.years','business.activeEntityBR','experience.accounting','experience.productionML','availability.schedule']}
    if not TRACK.search(normalized(title)):return result('discarded','outside_tracks',[title])
    if EXCLUDED.search(normalized(title)):return result('discarded','outside_seniority_or_specialty',[title])
    if re.search(r'front.?end',normalized(title)) and not re.search(r'back.?end|full.?stack',normalized(title)):
        return result('discarded','pure_frontend_outside_tracks',[title])
    if re.search(r'exclusiv[ao]|applicants only|afirmativ[ao]|affirmative',normalized(title)):
        return result('pending','affirmative_eligibility_not_confirmed',[title])
    if re.search(r'accounting experience',title,re.I) and not facts.get('experience.accounting',{}).get('value'):
        return result('pending','specialized_requirement_needs_evidence',[title])
    years=facts.get('experience.years',{}).get('value')
    for line in required.splitlines():
        if re.search(r'preferred|desej[aá]vel|nice.to.have',line,re.I):continue
        if (re.search(r'availability to work|disponibilidade para trabalhar',line,re.I)
                and re.search(r'\d{1,2}(?::\d{2})?\s*(?:am|pm|CST|EST|PST|UTC|GMT)\b',line,re.I)):
            accepted=facts.get('availability.schedule',{}).get('value',[])
            if not isinstance(accepted,list) or normalized(line) not in [normalized(v) for v in accepted if isinstance(v,str)]:
                return result('pending','mandatory_schedule_not_confirmed',[line])
        duration=re.search(r'(\d+)\s*(?:\+|[-–]\s*\d+)?\s*(?:anos|years)\b',line,re.I)
        if duration:
            if not isinstance(years,(int,float)):return result('pending','mandatory_duration_not_confirmed',[line])
            if int(duration[1])>years:return result('discarded','mandatory_duration_above_confirmed',[line])
    niche=re.search(r'\b(?:SAP|FICO|DMPS|ABAP|Workato|Elixir|Scala|Solidity|Rust|Salesforce|Protheus)\b',required,re.I)
    if niche:return result('pending','specialized_requirement_needs_evidence',[niche[0]])
    if re.search(r'ml engineering[^\n]{0,120}machine learning models in production',required,re.I) and not facts.get('experience.productionML',{}).get('value'):
        return result('pending','specific_production_ml_requirement',[line for line in required.splitlines() if re.search('machine learning models in production',line,re.I)])
    if re.search(r'\b(?:must|required|obrigatorio)\b.{0,160}\bcnpj\b',normalized(required)) and not facts.get('business.activeEntityBR',{}).get('value'):
        return result('pending','active_business_entity_not_confirmed',[line for line in required.splitlines() if 'CNPJ' in line])
    if re.search(r'(?:production.*LangGraph|LangGraph.*production)',required,re.I):
        return result('pending','specific_production_agent_requirement',[line for line in required.splitlines() if 'LangGraph' in line][:2])
    location=job.get('location','')
    # A remote arrangement does not override an explicit residence restriction.
    allowed_locations=re.search(r'(?:eligible locations?|allowed locations?|localidades eleg[ií]veis|localiza[çc][õo]es eleg[ií]veis)[ \t]*:[ \t]*(?:\r?\n[ \t]*)?([^\r\n]+)',description,re.I)
    if allowed_locations:
        places=normalized(allowed_locations[1])
        country_wide=places in ('br','brasil','brazil','anywhere in brazil','anywhere in brasil','todo o brasil')
        resident_br=normalized(facts.get('location.country',{}).get('value','')) in ('br','brasil','brazil')
        candidate_places=[normalized(facts.get(key,{}).get('value','')) for key in ('location.city','location.state')]
        listed_places={normalized(p) for p in re.split(r'[,;/|]|\b(?:or|and|ou|e)\b',allowed_locations[1],flags=re.I)}
        covered=any(p and p in listed_places for p in candidate_places)
        if not (resident_br and (country_wide or covered)):
            return result('pending','explicit_residence_restriction_not_supported',[allowed_locations[0],location])
    # Benefits that mention occasional remote work are not a job's arrangement.
    explicit_mode=re.search(r'(?:work\s*type|\bmode)\s*:\s*(remote|hybrid|on.?site)',description,re.I)
    hybrid=re.search(r'we are a hybrid|(?:minimum of|require).{0,30}\d+ days per week in the office',description,re.I)
    metadata_remote=job.get('platform',job.get('source'))=='lever' and job.get('workplaceType')=='remote'
    # A stated location policy is evidence; a generic country mention or
    # occasional remote-work benefit is not.
    home_policy=re.search(r'(?:can|may) (?:live|work) anywhere in (?:Brazil|Brasil)|(?:pode\w*|possibilidade de) (?:morar|trabalhar) (?:de |em )?qualquer (?:lugar|local) (?:do |no )?Brasil',description,re.I)
    remote=metadata_remote or bool(re.search(r'remot[eo]|home office',location,re.I)) or bool(explicit_mode and explicit_mode[1].lower()=='remote') or bool(home_policy)
    location_onsite=re.search(r'\bhybrid\b|hibrid[oa]|on.?site|presencial',normalized(location))
    if location_onsite:remote=False
    if hybrid or (explicit_mode and explicit_mode[1].lower()!='remote'):remote=False
    if not remote:
        if not re.search(r'Olinda|Recife',location,re.I):
            if hybrid or explicit_mode or location_onsite or job.get('workplaceType') in ('hybrid','on-site'):
                return result('discarded','onsite_or_hybrid_outside_home_region',[location,hybrid[0] if hybrid else explicit_mode[0] if explicit_mode else location_onsite[0] if location_onsite else job['workplaceType']])
            return result('pending','working_arrangement_needs_evidence',[location])
    country=job.get('country')
    explicit_brazil=country=='BR' or bool(re.search(r'\bBrazil\b|\bBrasil\b',location,re.I)) or bool(home_policy)
    if not explicit_brazil:return result('pending','geographic_eligibility_needs_evidence',[location,'country='+str(country)])
    salary=job.get('salaryRange') or {}
    if salary.get('currency')=='BRL' and salary.get('interval') in ('month','monthly') and salary.get('max',999999)<4000:
        return result('discarded','published_salary_below_floor',[str(salary)])
    if not description.strip():return result('pending','missing_requirements',[title])
    evidence=[title,location,'country='+str(country),required[:2000]]
    if allowed_locations:evidence.append(allowed_locations[0])
    if home_policy:evidence.append(home_policy[0])
    return result('eligible','requirements_supported_and_brazil_location',evidence)


def triage(data_dir):
    data=Path(data_dir);book=AnswerBook(data);store=AttemptStore(data)
    catalog=json.loads((data/'external/catalog.json').read_text(encoding='utf-8'))
    jobs=json.loads((data/'jobs.json').read_text(encoding='utf-8'))
    previous={identity(j):j for j in jobs.values() if j.get('source') in ('lever','greenhouse')}
    current={identity(j):j for j in catalog['jobs']}
    boards={(b['platform'],b['board']):b for b in catalog['boards']}
    protected={job_key(a['job_url']) for a in store.list() if a['state'] in ('confirmed','uncertain','running','submitted','awaiting_human')}
    output=[]
    for key,old in previous.items():
        job=current.get(key,old)
        if job_key(old['url']) in protected:
            decision={'state':'protected','reason':'existing_confirmed_uncertain_or_active','evidence':[old['url']],'fact_refs':[]}
        elif key not in current:
            board=boards.get(key[:2],{})
            if 'closed_imported' in board:decision={'state':'closed','reason':'not_in_current_complete_public_board','evidence':[],'fact_refs':[]}
            else:
                decision=qualify(job,book.facts)
                if decision['state']=='eligible':decision.update(state='pending',reason='availability_not_confirmed')
        else:decision=qualify(job,book.facts)
        decision.update(platform=key[0],board=key[1],externalId=key[2],url=job['url'],title=job['title'],location=job.get('location',''),
                        facts_version=book.reference_version(decision['fact_refs']))
        output.append(decision)
    result={'imported_unique':len(output),'states':dict(Counter(d['state'] for d in output)),
            'reasons':dict(Counter(d['reason'] for d in output)),'decisions':output}
    path=data/'external/triage.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    import os
    result=triage(Path(os.environ.get('LANGHIRE_DATA_DIR','.local')))
    print(json.dumps({k:v for k,v in result.items() if k!='decisions'},ensure_ascii=False),flush=True)
