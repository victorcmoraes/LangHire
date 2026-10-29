"""Refresh the owned catalog through public, read-only ATS job-board endpoints."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
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


def refresh_board(platform,board):
    if platform=='cornerstone':return cornerstone_jobs(board)
    url=(f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true' if platform=='greenhouse'
         else f'https://api.ashbyhq.com/posting-api/job-board/{board}' if platform=='ashby'
         else f'https://api.lever.co/v0/postings/{board}?mode=json')
    if platform=='lever' and board=='jobgether':url+='&location=Brazil&limit=100'
    req=urllib.request.Request(url,headers={'User-Agent':'LangHire-local-job-catalog/1.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=40) as response:data=json.load(response)
    rows=data['jobs'] if platform in ('greenhouse','ashby') else data
    result=[]
    for item in rows:
        if platform=='ashby' and item.get('isListed') is False:continue
        if platform=='greenhouse':
            job={'platform':platform,'source':platform,'board':board,'externalId':str(item['id']),
                 'title':item['title'],'url':f'https://job-boards.greenhouse.io/{board}/jobs/{item["id"]}',
                 'applyUrl':f'https://job-boards.greenhouse.io/{board}/jobs/{item["id"]}',
                 'location':item.get('location',{}).get('name',''),'description':plain(item.get('content','')),
                 'offices':item.get('offices',[]),'metadata':item.get('metadata',[])}
        elif platform=='ashby':
            job=ashby_job(item,board)
        else:
            job=lever_job(item,board)
        job['company']=board
        result.append(job)
    return result


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
    previous={identity(j):j for j in jobs.values() if j.get('source') in ('lever','greenhouse','ashby','cornerstone')}
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
                if board=='jobgether':details.update(scope='public location=Brazil, first 100; missing entries not declared closed',
                    not_refreshed_imported=sum(k not in active for k in previous if k[:2]==(platform,board)))
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
