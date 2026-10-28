"""Refresh the owned catalog through public, read-only ATS job-board endpoints."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import urllib.request


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


PLATFORMS=('greenhouse','lever','ashby','workable','smartrecruiters')


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


def fetch_json(url):
    req=urllib.request.Request(url,headers={'User-Agent':'LangHire-local-job-catalog/1.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=40) as response:return json.load(response)


# Structured arrangement only; Ashby's isRemote is also true for hybrid postings.
ASHBY_MODES={'Remote':'remote','Hybrid':'hybrid','OnSite':'on-site'}


def ashby_job(item,board):
    places=[item.get('address')]+[s.get('address') for s in item.get('secondaryLocations',[])]
    countries={((p or {}).get('postalAddress') or {}).get('addressCountry','') for p in places}
    location='; '.join([item.get('location','')]+[s.get('location','') for s in item.get('secondaryLocations',[])])
    return {'platform':'ashby','source':'ashby','board':board,'company':board,'externalId':item['id'],
            'title':item['title'],'url':item['jobUrl'],'applyUrl':item['applyUrl'],'location':location.strip('; '),
            'description':item.get('descriptionPlain') or plain(item.get('descriptionHtml','')),
            'country':'BR' if countries&{'Brazil','Brasil','BR'} else None,
            'workplaceType':ASHBY_MODES.get(item.get('workplaceType'),''),'department':item.get('department','')}


def workable_job(item,board):
    places=item.get('locations') or [{'country':item.get('country',''),'city':item.get('city','')}]
    location='; '.join(', '.join(p for p in (l.get('city'),l.get('region'),l.get('country')) if p) for l in places if not l.get('hidden'))
    return {'platform':'workable','source':'workable','board':board,'company':board,'externalId':item['shortcode'],
            'title':item['title'],'url':item['url'],'applyUrl':item['application_url'],'location':location,
            'description':plain(item.get('description','')),
            'country':'BR' if any(l.get('countryCode')=='BR' for l in places) else None,
            'workplaceType':'remote' if item.get('telecommuting') else '','department':item.get('department','')}


def smartrecruiters_job(item,board):
    place=item.get('location',{})
    sections=item.get('jobAd',{}).get('sections',{})
    description='\n\n'.join((s.get('title') or '')+'\n'+plain(s.get('text','')) for k,s in sections.items() if k!='companyDescription')
    return {'platform':'smartrecruiters','source':'smartrecruiters','board':board,'company':board,'externalId':item['id'],
            'title':item['name'],'url':item['postingUrl'],'applyUrl':item['applyUrl'],'location':place.get('fullLocation',''),
            'description':description.strip(),'country':(place.get('country') or '').upper() or None,
            'workplaceType':'remote' if place.get('remote') else 'hybrid' if place.get('hybrid') else 'on-site'}


# Company boards can hold thousands of global postings; only Brazilian ones are
# listed, and a board too large to read completely fails instead of closing jobs.
SMARTRECRUITERS_PAGES=5


def smartrecruiters_board(board,relevant=None):
    listed=[]
    for page in range(SMARTRECRUITERS_PAGES):
        data=fetch_json(f'https://api.smartrecruiters.com/v1/companies/{board}/postings?country=br&limit=100&offset={page*100}')
        listed+=data['content']
        if len(listed)>=data['totalFound'] or not data['content']:break
    else:
        if len(listed)<data['totalFound']:raise RuntimeError('SmartRecruiters board exceeds the bounded page count')
    # Descriptions need one request per posting; titles outside the tracks skip it.
    return [smartrecruiters_job(fetch_json(f'https://api.smartrecruiters.com/v1/companies/{board}/postings/{item["id"]}'),board)
            if relevant is None or relevant(item['name']) else
            smartrecruiters_job(dict(item,postingUrl=f'https://jobs.smartrecruiters.com/{board}/{item["id"]}',applyUrl=''),board)
            for item in listed]


def refresh_board(platform,board,relevant=None):
    if platform=='ashby':
        return [ashby_job(item,board) for item in fetch_json(f'https://api.ashbyhq.com/posting-api/job-board/{board}')['jobs'] if item.get('isListed',True)]
    if platform=='workable':
        return [workable_job(item,board) for item in fetch_json(f'https://apply.workable.com/api/v1/widget/accounts/{board}?details=true')['jobs']]
    if platform=='smartrecruiters':return smartrecruiters_board(board,relevant)
    url=(f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true' if platform=='greenhouse'
         else f'https://api.lever.co/v0/postings/{board}?mode=json')
    if platform=='lever' and board=='jobgether':url+='&location=Brazil&limit=100'
    data=fetch_json(url)
    rows=data['jobs'] if platform=='greenhouse' else data
    result=[]
    for item in rows:
        if platform=='greenhouse':
            job={'platform':platform,'source':platform,'board':board,'externalId':str(item['id']),
                 'title':item['title'],'url':f'https://job-boards.greenhouse.io/{board}/jobs/{item["id"]}',
                 'applyUrl':f'https://job-boards.greenhouse.io/{board}/jobs/{item["id"]}',
                 'location':item.get('location',{}).get('name',''),'description':plain(item.get('content','')),
                 'offices':item.get('offices',[]),'metadata':item.get('metadata',[])}
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
    previous={identity(j):j for j in jobs.values() if j.get('source') in ('lever','greenhouse')}
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
