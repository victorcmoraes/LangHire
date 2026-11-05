"""Refresh the owned catalog through public, read-only ATS job-board endpoints."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import quote,urlsplit
import urllib.request
from .cornerstone import board_parts,job_url


class Text(HTMLParser):
    def __init__(self):super().__init__();self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('p','li','div','br','h1','h2','h3','h4','h5','section'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('p','li','div','h1','h2','h3','h4','h5','section'):self.parts.append('\n')
    def handle_data(self,data):self.parts.append(data)


def plain(html):
    parser=Text();parser.feed(unescape(html or ''))
    text=''.join(parser.parts).replace('\r\n','\n')
    return re.sub(r'\n{3,}','\n\n','\n'.join(re.sub(r'[ \t]+',' ',line).strip() for line in text.splitlines())).strip()


def identity(job):
    return (job.get('platform') or job.get('source'),job.get('board'),str(job.get('externalId','')))


def lever_job(item,board):
    description=[item.get('descriptionPlain') or plain(item.get('description',''))]
    description += [section.get('text','')+'\n'+plain(section.get('content','')) for section in item.get('lists',[])]
    description += [item.get('additionalPlain') or plain(item.get('additional',''))]
    return {'platform':'lever','source':'lever','board':board,'company':board,'externalId':item['id'],
            'title':item['text'],'url':item['hostedUrl'],'applyUrl':item['applyUrl'],
            'location':item.get('categories',{}).get('location',''),'description':'\n\n'.join(description),
            'categories':item.get('categories',{}),'country':item.get('country'),
            'workplaceType':item.get('workplaceType',''),'salaryRange':item.get('salaryRange')}


def ashby_job(item,board):
    mode={'Remote':'remote','Hybrid':'hybrid','OnSite':'on-site'}.get(item.get('workplaceType'),'')
    locations=[item.get('location','')]+[place.get('location','') for place in item.get('secondaryLocations',[])]
    # Qualification reads stated locations and mode; isRemote also includes hybrid.
    location='; '.join(dict.fromkeys(place for place in locations if place))
    if mode:location+=' ('+mode+')'
    return {'platform':'ashby','source':'ashby','board':board,'company':board,'externalId':str(item['id']),
            'title':item['title'],'url':item['jobUrl'],'applyUrl':item.get('applyUrl'),
            'location':location,'description':item.get('descriptionPlain') or plain(item.get('descriptionHtml','')),
            'workplaceType':mode,'secondaryLocations':item.get('secondaryLocations',[]),'address':item.get('address')}


def workable_job(item,board,company):
    mode={'remote':'remote','hybrid':'hybrid','on_site':'on-site'}.get(item.get('workplace_type'),'')
    if not item.get('workplace_type') and item.get('telecommuting') is True:mode='remote'
    places=item.get('locations') or [{'city':item.get('city'),'region':item.get('state'),'country':item.get('country')}]
    locations=[', '.join(place[k] for k in ('city','region','country') if place.get(k)) for place in places]
    location='; '.join(dict.fromkeys(place for place in locations if place))
    if mode:location+=' ('+mode+')'
    url=f'https://apply.workable.com/{board}/j/{item["shortcode"]}'
    return {'platform':'workable','source':'workable','board':board,'company':company or board,
            'externalId':str(item['shortcode']),'title':item['title'],'url':url,'applyUrl':url+'/apply',
            'location':location,'description':plain(item.get('description','')),'workplaceType':mode}


def recruitee_job(item,board):
    mode='hybrid' if item.get('hybrid') else 'on-site' if item.get('on_site') else 'remote' if item.get('remote') else ''
    location=', '.join(dict.fromkeys(str(item[k]) for k in ('location','city','country','country_code') if item.get(k)))
    if mode:location+=' ('+mode+')'
    return {'platform':'recruitee','source':'recruitee','board':board,'company':board,'externalId':str(item['id']),
            'job_key':f'recruitee:{board}:{item["id"]}','title':item['title'],'url':item['careers_url'],
            'applyUrl':item.get('careers_apply_url'),'location':location,'workplaceType':mode,'country':item.get('country_code'),
            'description':plain(item.get('description',''))+'\n\n'+plain(item.get('requirements','')),
            'employment_type_code':item.get('employment_type_code'),'published_at':item.get('published_at'),
            'recruitee_slug':item['slug'],'recruitee_questions':{k:item.get(k) for k in
                ('open_questions','options_cv','options_phone','options_cover_letter')}}


def cornerstone_job(item,board):
    tenant,site=board_parts(board)
    locations=[', '.join(str(place.get(k) or '').strip().title() if k=='city' else str(place.get(k) or '').strip()
                         for k in ('city','state') if place.get(k)) for place in item.get('locations',[])]
    return {'platform':'cornerstone','source':'cornerstone','board':board,'company':tenant,
            'externalId':str(item['requisitionId']),'title':item['displayJobTitle'],
            'url':job_url(tenant,site,item['requisitionId']),'location':'; '.join(dict.fromkeys(p for p in locations if p)),
            'description':plain(item.get('externalDescription','')),
            'postingEffectiveDate':item.get('postingEffectiveDate'),'postingExpirationDate':item.get('postingExpirationDate')}


def cornerstone_jobs(board):
    tenant,site=board_parts(board)
    headers={'User-Agent':'LangHire-local-job-catalog/1.0','Accept':'application/json'}
    req=urllib.request.Request(f'https://{tenant}.csod.com/ux/ats/careersite/{site}/home?c={tenant}',headers=headers)
    with urllib.request.urlopen(req,timeout=40) as response:html=unescape(response.read().decode('utf-8'))
    context={}
    for key in ('token','cloud'):
        match=re.search(r'"'+key+r'"\s*:\s*("(?:[^"\\]|\\.)*")',html)
        if not match:raise ValueError('Missing public Cornerstone context')
        context[key]=json.loads(match[1])
    # Only send the anonymous token to an official regional API host.
    cloud=urlsplit(context['cloud'])
    if (cloud.scheme!='https' or not re.fullmatch(r'[a-z0-9-]+\.api\.csod\.com',cloud.hostname or '')
            or cloud.port not in (None,443) or cloud.username or cloud.password or cloud.path not in ('','/') or cloud.query or cloud.fragment):
        raise ValueError('Invalid public Cornerstone cloud')
    if not context['token']:raise ValueError('Missing public Cornerstone token')
    headers.update(Authorization='Bearer '+context['token'],**{'Content-Type':'application/json'})
    result=[];seen=set();page=1
    while True:
        body={'careerSiteId':site,'careerSitePageId':site,'pageNumber':page,'pageSize':100,'cultureId':12,
              'searchText':'','cultureName':'pt-BR','states':[],'countryCodes':[],'cities':[],'placeID':'',
              'radius':None,'postingsWithinDays':None,'customFieldCheckboxKeys':[],'customFieldDropdowns':[],'customFieldRadios':[]}
        req=urllib.request.Request(context['cloud'].rstrip('/')+'/rec-job-search/external/jobs',data=json.dumps(body).encode(),headers=headers)
        with urllib.request.urlopen(req,timeout=40) as response:data=json.load(response)['data']
        rows=data['requisitions'];fresh=[item for item in rows if str(item['requisitionId']) not in seen]
        for item in fresh:
            seen.add(str(item['requisitionId']));result.append(cornerstone_job(item,board))
        if len(seen)>=data['totalCount']:return result
        # Incomplete pages must not declare missing jobs closed.
        if not fresh:raise ValueError('Incomplete public Cornerstone pagination')
        page+=1


GUPY_MODES={'remote':'remote','hybrid':'hybrid','on-site':'on-site','onsite':'on-site'}


def next_data(url):
    """Gupy renders its public pages server-side; their data sits in Next.js's __NEXT_DATA__ block."""
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (LangHire-local-job-catalog/1.0)','Accept':'text/html'})
    with urllib.request.urlopen(req,timeout=40) as response:html=response.read().decode('utf-8')
    match=re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',html,re.S)
    if not match:raise ValueError('Gupy page without __NEXT_DATA__')
    return json.loads(match[1])['props']['pageProps']


def gupy_job(item,board):
    mode=GUPY_MODES.get(item.get('workplaceType') or '','')
    place=', '.join(p for p in (item.get('addressCity'),item.get('addressStateShortName') or item.get('addressState')) if p)
    location=(place or 'Brasil')+(' ('+mode+')' if mode else '')
    description='\n\n'.join(plain(item.get(k) or '') for k in ('description','responsibilities','prerequisites','relevantExperiences'))
    return {'platform':'gupy','source':'gupy','board':board,'company':(item.get('careerPage') or {}).get('name') or board,
            'externalId':str(item['id']),'title':item['name'].strip(),'url':f'https://{board}.gupy.io/jobs/{item["id"]}',
            'applyUrl':f'https://{board}.gupy.io/jobs/{item["id"]}','location':location,'workplaceType':mode,
            # Gupy is a Brazilian ATS; postings name another country explicitly when they are elsewhere.
            'country':item.get('addressCountryShortName') or 'BR','description':description,
            'jobType':item.get('jobType'),'gupy_questions':item.get('questionForm'),'published_at':item.get('publishedAt')}


def gupy_jobs(board,tracks=None):
    """The company page lists every opening; details are fetched only for titles on the candidate's tracks."""
    from .job_tracks import load_job_tracks,matches_track
    tracks=tracks or load_job_tracks()
    listing=next_data(f'https://{board}.gupy.io/').get('jobs') or []
    result=[]
    for item in listing:
        workplace=item.get('workplace') or {};address=workplace.get('address') or {}
        # Talent pools are not openings; applying there sends nothing to a hiring process.
        if item.get('type')=='vacancy_type_talent_pool':continue
        if not matches_track(item.get('title',''),tracks):
            result.append({'platform':'gupy','source':'gupy','board':board,'company':board,'externalId':str(item['id']),
                           'title':item.get('title',''),'url':f'https://{board}.gupy.io/jobs/{item["id"]}',
                           'location':', '.join(p for p in (address.get('city'),address.get('stateShortName')) if p) or address.get('country',''),
                           'workplaceType':GUPY_MODES.get(workplace.get('workplaceType') or '',''),'country':'BR','description':''})
            continue
        detail=next_data(f'https://{board}.gupy.io/jobs/{item["id"]}').get('job') or {}
        if detail.get('status')=='published' and str(detail.get('id'))==str(item['id']):result.append(gupy_job(detail,board))
    return result


def refresh_board(platform,board):
    if platform=='cornerstone':return cornerstone_jobs(board)
    if platform=='gupy':return gupy_jobs(board)
    url=(f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true' if platform=='greenhouse'
         else f'https://api.ashbyhq.com/posting-api/job-board/{board}' if platform=='ashby'
         else f'https://www.workable.com/api/accounts/{board}?details=true' if platform=='workable'
         else f'https://{board}.recruitee.com/api/offers/' if platform=='recruitee'
         else f'https://api.lever.co/v0/postings/{board}?mode=json')
    if platform=='lever' and board=='jobgether':url+='&location=Brazil&limit=100'
    req=urllib.request.Request(url,headers={'User-Agent':'LangHire-local-job-catalog/1.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=40) as response:data=json.load(response)
    rows=data['offers'] if platform=='recruitee' else data['jobs'] if platform in ('greenhouse','ashby','workable') else data
    result=[]
    for item in rows:
        if platform=='recruitee' and item.get('status')!='published':continue
        if platform=='ashby' and item.get('isListed') is False:continue
        if platform=='workable' and not item.get('published_on'):continue
        if platform=='greenhouse':
            job={'platform':platform,'source':platform,'board':board,'externalId':str(item['id']),
                 'title':item['title'],'url':f'https://job-boards.greenhouse.io/{board}/jobs/{item["id"]}',
                 'applyUrl':f'https://job-boards.greenhouse.io/{board}/jobs/{item["id"]}',
                 'location':item.get('location',{}).get('name',''),'description':plain(item.get('content','')),
                 'offices':item.get('offices',[]),'metadata':item.get('metadata',[])}
        elif platform=='recruitee':
            job=recruitee_job(item,board)
        elif platform=='ashby':
            job=ashby_job(item,board)
        elif platform=='workable':
            job=workable_job(item,board,data.get('name'))
        else:
            job=lever_job(item,board)
        if platform!='workable':job['company']=board
        result.append(job)
    return result


def check_missing_jobgether(previous,rows,closed=()):
    """Only an individual 404 proves closure on this incomplete board; stop on other errors."""
    active={identity(j) for j in rows};closed=set(closed)-{key[2] for key in active};checked=0;error={};slow=0
    for key in sorted(previous):
        if key[:2]!=('lever','jobgether') or key in active or key[2] in closed:continue
        if checked>=150:break
        url='https://api.lever.co/v0/postings/jobgether/'+quote(key[2],safe='')
        req=urllib.request.Request(url,headers={'User-Agent':'LangHire-local-job-catalog/1.0','Accept':'application/json'})
        checked+=1
        try:
            with urllib.request.urlopen(req,timeout=10) as response:
                if response.status!=200:raise ValueError('Unexpected posting response: '+str(response.status))
        except HTTPError as failure:
            if failure.code==404:closed.add(key[2]);continue
            error={'posting_check_error':type(failure).__name__,'posting_check_http_status':failure.code};break
        except TimeoutError as failure:
            # Lever's posting endpoint stalls intermittently; a slow reply proves nothing, so skip it unless it keeps happening.
            slow+=1
            if slow<3:continue
            error={'posting_check_error':type(failure).__name__};break
        except Exception as failure:
            error={'posting_check_error':type(failure).__name__};break
    return dict(posting_not_found=sorted(closed),posting_checks=checked,**error)


def merge_filtered_jobgether(data_dir):
    """Recover the imported Brazilian subset without downloading a global aggregator."""
    data_dir=Path(data_dir);path=data_dir/'external/catalog.json'
    result=json.loads(path.read_text(encoding='utf-8'))
    raw=json.loads((data_dir/'external/jobgether-brazil.json').read_text(encoding='utf-8'))
    jobs=json.loads((data_dir/'jobs.json').read_text(encoding='utf-8'))
    previous={identity(j):j for j in jobs.values() if j.get('source')=='lever' and j.get('board')=='jobgether'}
    matches=[]
    for item in raw:
        normalized=lever_job(item,'jobgether');key=identity(normalized)
        if key in previous:matches.append(dict(normalized,legacy=previous[key]))
    result['jobs']=[j for j in result['jobs'] if j['board']!='jobgether']+matches
    result['boards']=[b for b in result['boards'] if b['board']!='jobgether']+[{
        'platform':'lever','board':'jobgether','scope':'public location=Brazil, first 100; missing entries not declared closed',
        'active_imported':len(matches),'outside_brazil_imported':sum(not re.search('Brazil|Brasil| BR ',j.get('location','')) for j in previous.values())}]
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def refresh_imported(data_dir):
    data_dir=Path(data_dir);jobs=json.loads((data_dir/'jobs.json').read_text(encoding='utf-8'))
    previous={identity(j):j for j in jobs.values() if j.get('source') in ('lever','greenhouse','ashby','cornerstone','workable','recruitee','gupy')}
    path=data_dir/'external/catalog.json'
    catalog=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'jobs':[],'boards':[]}
    known={identity(j):j for j in catalog['jobs'] if identity(j)[:2]==('lever','jobgether')}
    known.update({key:j for key,j in previous.items() if key[:2]==('lever','jobgether')})
    closed=next((b.get('posting_not_found',[]) for b in catalog['boards'] if (b['platform'],b['board'])==('lever','jobgether')),[])
    boards=sorted({(key[0],key[1]) for key in previous if key[1]})
    result={'imported_unique':len(previous),'boards':[],'jobs':[]}
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks={pool.submit(refresh_board,*board):board for board in boards}
        for future in as_completed(tasks):
            platform,board=tasks[future]
            try:
                rows=future.result();active={identity(j):j for j in rows}
                retained=[dict(j,legacy=previous[identity(j)]) for j in rows if identity(j) in previous]
                result['jobs'].extend(retained)
                details={'platform':platform,'board':board,'active_public':len(rows),'active_imported':len(retained)}
                if (platform,board)==('lever','jobgether'):
                    details.update(scope='public location=Brazil, first 100; closure requires individual 404',
                        **check_missing_jobgether(known,rows,closed))
                    result['jobs'].extend(j for key,j in known.items() if key not in active and key[2] not in details['posting_not_found'])
                    details['not_refreshed_imported']=sum(k not in active and k[2] not in details['posting_not_found'] for k in known)
                else:details['closed_imported']=sum(k not in active for k in previous if k[:2]==(platform,board))
                result['boards'].append(details)
            except Exception as error:result['boards'].append({'platform':platform,'board':board,'error':type(error).__name__})
    root=data_dir/'external';root.mkdir(exist_ok=True)
    path=root/'catalog.json';temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    return result


if __name__=='__main__':
    import os
    result=refresh_imported(Path(os.environ.get('LANGHIRE_DATA_DIR','.local')))
    print(json.dumps({'imported_unique':result['imported_unique'],'active_imported':len(result['jobs']),'boards':result['boards']},ensure_ascii=False),flush=True)
