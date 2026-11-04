"""Auditable triage of the imported catalog; no browser/model can assert eligibility."""
from collections import Counter
import json
from pathlib import Path
import re
from .application_safety import AttemptStore,job_key
from .easy_answers import AnswerBook,normalized
from .external_catalog import identity
from .job_tracks import load_job_tracks,matches_track


def required_text(description):
    lines=description.splitlines();start=None;end=len(lines)
    for i,line in enumerate(lines):
        n=normalized(line)
        if start is None and len(n)<110 and re.match(r'^(requirements|requisitos|qualifications|work experience|must have experience|required skills|what you need|who you are|o que esperamos|o que buscamos|perfil que buscamos|what you bring|what we are looking for|what were looking for|requisitos para este|competencias requeridas)\b',n):start=i
        elif start is not None and re.match(r'^(benefits|beneficios|nossos beneficios|(?:opcional )?diferenciais|sera considerado um diferencial|desired|nice to have|bonus|preferred skills|preferred qualifications|what we offer|english level|legal|o que aumenta|o que oferecemos|you may stand out)\b',n):end=i;break
    return '\n'.join(lines[start:end]) if start is not None else description


def home_cities(facts):
    """Cities the candidate commutes to; the residence city alone when no list is confirmed."""
    cities=facts.get('location.commuteCities',{}).get('value') or [facts.get('location.city',{}).get('value')]
    return [c for c in cities if isinstance(c,str) and c.strip()]


def qualify(job,facts,tracks=None):
    tracks=load_job_tracks() if tracks is None else tracks
    title=job['title'];description=job.get('description','');required=required_text(description)
    def result(state,reason,evidence):
        return {'state':state,'reason':reason,'evidence':evidence,'fact_refs':['location.country','location.city','location.state','skills','skills.confirmed','skills.commonExperienceAuthority','experience.years','business.activeEntityBR','experience.accounting','experience.productionML','availability.schedule','languages','level.english','level.spanish','level.german','level.french','level.italian','level.mandarin','level.japanese','level.dutch']}
    if not matches_track(title,tracks):return result('discarded','outside_tracks',[title])
    if tracks['exclude'].search(normalized(title)):return result('discarded','outside_seniority_or_specialty',[title])
    if not tracks['allow_mobile'] and re.search(r'\breact native\b|\bflutter\b|\bswiftui\b',normalized(title)):
        return result('discarded','mobile_outside_tracks',[title])
    if not tracks['allow_pure_frontend'] and re.search(r'front.?end',normalized(title)) and not re.search(r'back.?end|full.?stack',normalized(title)):
        return result('discarded','pure_frontend_outside_tracks',[title])
    if re.search(r'exclusiv[ao]|applicants only|afirmativ[ao]|affirmative|\bpcd\b|pessoas? com deficiencia',normalized(title)):
        return result('pending','affirmative_eligibility_not_confirmed',[title])
    if re.search(r'accounting experience',title,re.I) and not facts.get('experience.accounting',{}).get('value'):
        return result('pending','specialized_requirement_needs_evidence',[title])
    years=facts.get('experience.years',{}).get('value')
    for match in re.finditer(r'(\d+)\s*(?:\+|[-–]\s*\d+)?\s*(?:anos|years)\b',required,re.I):
        line=required[max(required.rfind('\n',0,match.start())+1,match.start()-100):required.find('\n',match.end()) if '\n' in required[match.end():] else len(required)]
        if re.search(r'preferred|desej[aá]vel|nice.to.have',line,re.I):continue
        if not isinstance(years,(int,float)):return result('pending','mandatory_duration_not_confirmed',[line])
        if int(match[1])>years:return result('discarded','mandatory_duration_above_confirmed',[line])
    languages={'german':r'German|alem[aã]o','french':r'French|franc[eê]s','italian':r'Italian|italiano',
               'mandarin':r'Mandarin|mandarim','japanese':r'Japanese|japon[eê]s','dutch':r'Dutch|holand[eê]s'}
    for language,pattern in languages.items():
        for line in required.splitlines():
            if re.search(r'\b(?:'+pattern+r')\b',line,re.I) and not re.search(r'preferred|optional|desej[aá]vel|nice.to.have|advantage|diferencial',line,re.I):
                if not facts.get('level.'+language,{}).get('value'):return result('pending','mandatory_language_not_confirmed',[line])
    generic_hours=re.search(r'[^\n]*(?:US business hours|US working hours|hor[aá]rio (?:comercial )?americano)[^\n]*',required,re.I)
    if generic_hours:
        accepted=facts.get('availability.schedule',{}).get('value',[])
        if not isinstance(accepted,list) or normalized(generic_hours[0]) not in [normalized(v) for v in accepted if isinstance(v,str)]:
            return result('pending','mandatory_schedule_not_confirmed',[generic_hours[0]])
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
    niche=re.search(r'\b(?:SAP|FICO|DMPS|ABAP|Workato|Elixir|Scala|Solidity|Rust|Salesforce|Protheus|Delphi|COBOL|HubSpot|Service\s*Now|AEM)\b',title+'\n'+required,re.I)
    if niche:return result('pending','specialized_requirement_needs_evidence',[niche[0]])
    if re.search(r'ml engineering[^\n]{0,120}machine learning models in production',required,re.I) and not facts.get('experience.productionML',{}).get('value'):
        return result('pending','specific_production_ml_requirement',[line for line in required.splitlines() if re.search('machine learning models in production',line,re.I)])
    if re.search(r'\b(?:must|required|obrigatorio)\b.{0,160}\bcnpj\b',normalized(required)) and not facts.get('business.activeEntityBR',{}).get('value'):
        return result('pending','active_business_entity_not_confirmed',[line for line in required.splitlines() if 'CNPJ' in line])
    if re.search(r'(?:production.*LangGraph|LangGraph.*production)',required,re.I):
        return result('pending','specific_production_agent_requirement',[line for line in required.splitlines() if 'LangGraph' in line][:2])
    arrangement=job.get('arrangement_evidence') or {}
    quotes=[arrangement[k] for k in ('mode_quote','brazil_quote') if arrangement.get(k)]
    if arrangement.get('brazil')=='excluded':return result('discarded','brazil_excluded_by_posting',quotes)
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
            return result('pending','explicit_residence_restriction_not_supported',[allowed_locations[0],location]+quotes)
    # Benefits that mention occasional remote work are not a job's arrangement.
    explicit_mode=re.search(r'(?:work\s*type|\bmode|modalidade|modelo(?: de (?:trabalho|atua[çc][ãa]o))?)\s*:\s*(remot[eo]|hybrid|h[íi]brid[oa]|on.?site|presencial)',description,re.I)
    hybrid=re.search(r'we are a hybrid|(?:minimum of|require).{0,30}\d+ days per week in the office',description,re.I)
    # LinkedIn's #LI-Remote line is the recruiter's workplace tag, like Lever's workplaceType.
    metadata_remote=(job.get('platform',job.get('source')) in ('lever','linkedin') and job.get('workplaceType')=='remote') or bool(re.search(r'^\s*#LI-Remote\b\W*$',description,re.I|re.M))
    # A stated location policy is evidence; a generic country mention or
    # occasional remote-work benefit is not.
    home_policy=re.search(r'(?:can|may) (?:live|work) anywhere in (?:Brazil|Brasil)|(?:pode\w*|possibilidade de) (?:morar|trabalhar) (?:de |em )?qualquer (?:lugar|local) (?:do |no )?Brasil',description,re.I)
    remote_modes=('remote','remoto','remota')
    remote=arrangement.get('mode')=='remote' or metadata_remote or bool(re.search(r'\bremot[eo]\b|home office',location+'\n'+title,re.I)) or bool(explicit_mode and normalized(explicit_mode[1]) in remote_modes) or bool(home_policy)
    title_onsite=re.search(r'\bhybrid\b|hibrid[oa]|on.?site|presencial',normalized(title))
    location_onsite=re.search(r'\bhybrid\b|hibrid[oa]|on.?site|presencial',normalized(location))
    if location_onsite or title_onsite:remote=False
    if arrangement.get('mode') in ('hybrid','onsite'):remote=False
    if hybrid or (explicit_mode and normalized(explicit_mode[1]) not in remote_modes):remote=False
    if not remote:
        if not any(re.search(r'\b'+re.escape(city)+r'\b',location,re.I) for city in home_cities(facts)):
            if arrangement.get('mode') in ('hybrid','onsite'):
                return result('discarded','onsite_or_hybrid_outside_home_region',[location]+quotes)
            if hybrid or explicit_mode or location_onsite or title_onsite or job.get('workplaceType') in ('hybrid','on-site'):
                return result('discarded','onsite_or_hybrid_outside_home_region',[location,hybrid[0] if hybrid else explicit_mode[0] if explicit_mode else location_onsite[0] if location_onsite else title_onsite[0] if title_onsite else job['workplaceType']]+quotes)
            return result('pending','working_arrangement_needs_evidence',[location]+quotes)
    country=job.get('country')
    # "Recife, PE; São Paulo, SP": every listed place carries a Brazilian state code.
    places=[p.strip() for p in location.split(';') if p.strip()]
    brazilian_states=bool(places) and all(re.fullmatch(r'[^,;]+,\s*(?:AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)',p) for p in places)
    explicit_brazil=brazilian_states or arrangement.get('brazil')=='allowed' or country=='BR' or bool(re.search(r'\bBrazil\b|\bBrasil\b',location,re.I)) or bool(home_policy)
    # A remote job open to all of Latin America or worldwide covers a Brazilian resident unless it excludes Brazil.
    # "Americas" stays pending: postings use it for the US and Canada as often as for the whole continent.
    excluded=re.search(r'(?:except|excluding|exclude[sd]?|exceto|excluindo|not (?:open|available|eligible) (?:to|in|for))[^.\n]{0,80}\bbra[sz]il\b',normalized(description))
    if excluded:return result('discarded','brazil_excluded_by_posting',[excluded[0]]+quotes)
    regional=None
    if not explicit_brazil:
        named_elsewhere=re.search(r'\b(?:us|usa|united states|canada|europe|emea|uk|united kingdom|apac|india|mexico|colombia|argentina|chile|peru)\b',normalized(location))
        latam=r'latam|latin america|america latina|south america|america do sul'
        regional=None if named_elsewhere else (re.search(r'\b(?:'+latam+r'|worldwide|anywhere(?! in\b)|global)\b',normalized(location))
                                               or re.search(r'\b(?:'+latam+r')\b',normalized(title)))
    if not explicit_brazil and not regional:return result('pending','geographic_eligibility_needs_evidence',[location,'country='+str(country)]+quotes)
    if regional:quotes=quotes+['regional_remote='+regional[0]]
    salary=job.get('salaryRange') or {}
    floor=facts.get('salary.minimumBrlMonthly',{}).get('value')
    if isinstance(floor,(int,float)) and salary.get('currency')=='BRL' and salary.get('interval') in ('month','monthly') and salary.get('max',float('inf'))<floor:
        return result('discarded','published_salary_below_floor',[str(salary)]+quotes)
    if not description.strip():return result('pending','missing_requirements',[title])
    evidence=[title,location,'country='+str(country),required[:2000]]
    evidence.extend(quotes)
    if allowed_locations:evidence.append(allowed_locations[0])
    if home_policy:evidence.append(home_policy[0])
    return result('eligible','requirements_supported_and_brazil_location',evidence)


def triage(data_dir):
    from .arrangement_evidence import load_cache,cache_key,verified
    cache=load_cache(data_dir)
    data=Path(data_dir);tracks=load_job_tracks(data);book=AnswerBook(data);store=AttemptStore(data)
    catalog=json.loads((data/'external/catalog.json').read_text(encoding='utf-8'))
    jobs=json.loads((data/'jobs.json').read_text(encoding='utf-8'))
    previous={identity(j):j for j in jobs.values() if j.get('source') in ('lever','greenhouse','ashby','cornerstone','workable','recruitee')}
    current={identity(j):j for j in catalog['jobs']}
    boards={(b['platform'],b['board']):b for b in catalog['boards']}
    protected={job_key(a['job_url']) for a in store.list() if a['state'] in ('confirmed','uncertain','running','submitted','awaiting_human')}
    output=[]
    for key,old in previous.items():
        job=dict(current.get(key,old))
        job.pop('arrangement_evidence',None)
        entry=cache.get(cache_key(job))
        if entry is not None:job['arrangement_evidence']=verified(job,entry)
        if job_key(old['url']) in protected:
            decision={'state':'protected','reason':'existing_confirmed_uncertain_or_active','evidence':[old['url']],'fact_refs':[]}
        elif key not in current:
            board=boards.get(key[:2],{})
            if key[2] in board.get('posting_not_found',[]):decision={'state':'closed','reason':'posting_not_found','evidence':[],'fact_refs':[]}
            elif 'closed_imported' in board:decision={'state':'closed','reason':'not_in_current_complete_public_board','evidence':[],'fact_refs':[]}
            else:
                decision=qualify(job,book.facts,tracks)
                if decision['state']=='eligible':decision.update(state='pending',reason='availability_not_confirmed')
        else:decision=qualify(job,book.facts,tracks)
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
