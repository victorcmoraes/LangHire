"""Find hosted ATS boards of companies that hire in Brazil, from public aggregator listings.

Aggregators (Himalayas, Workable's job search, Get on Board) list who is hiring remotely in Brazil but hide the
employer's own application link, so their company names are probed as Greenhouse, Lever, Ashby and Workable
boards. Brazilian GitHub job boards (backend-br/vagas, frontendbr/vagas) carry official links in the issue
text, and Gupy's public portal search names each company's career subdomain. Every candidate still goes through the board validation and qualification used by `boards suggest`;
nothing here applies anywhere."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import time
import unicodedata
import urllib.request
from urllib.parse import urlencode

from .application_safety import SafetyError, job_key, now
from .easy_answers import AnswerBook
from .external_boards import LINK, add_boards, evaluate_board, registry, validate_board, write_json
from .external_discovery import board_spec
from .job_tracks import load_job_tracks, matches_track

HEADERS = {'User-Agent':'LangHire-local-job-catalog/1.0', 'Accept':'application/json'}
TERMS = ('developer', 'desenvolvedor', 'backend', 'frontend', 'full stack', 'software engineer', 'qa', 'data engineer', 'devops')
GITHUB_REPOS = ('backend-br/vagas', 'frontendbr/vagas')
PROBED = ('greenhouse', 'lever', 'ashby', 'workable')
LEGAL = {'inc', 'llc', 'ltd', 'ltda', 'sa', 'corp', 'corporation', 'gmbh', 'co', 'company', 'me', 'eireli'}
DESCRIPTIVE = {'group', 'grupo', 'technologies', 'technology', 'tecnologia', 'tech', 'labs', 'hq', 'io', 'brasil', 'brazil', 'software', 'digital'}


def fetch(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as response:
        return json.load(response)


def slugs(name):
    """'Acme Labs, Inc.' -> ['acmelabs', 'acme-labs', 'acme']; ATS boards use one of these forms."""
    plain = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode().lower()
    plain = re.sub(r'\(.*?\)', ' ', plain).replace('&', ' and ').replace('s.a.', 'sa')
    words = [w for w in re.split(r'[^a-z0-9]+', plain) if w and w not in LEGAL]
    core = [w for w in words if w not in DESCRIPTIVE] or words
    found = []
    for group in (words, core):
        if group:found += [''.join(group), '-'.join(group)]
    if len(core) > 1 and len(core[0]) > 3:found.append(core[0])
    return [s for s in dict.fromkeys(found) if 2 < len(s) <= 60]


def himalayas(tracks, terms=TERMS, pages=5):
    companies = {}
    for term in terms:
        for page in range(1, pages+1):
            data = fetch('https://himalayas.app/jobs/api/search?'+urlencode({'country':'Brazil', 'q':term, 'page':page}))
            for job in data.get('jobs', []):
                if matches_track(job.get('title', ''), tracks):
                    companies.setdefault(job['companyName'], {'source':'himalayas', 'titles':[]})['titles'].append(job['title'])
            if page*data.get('limit', 20) >= data.get('totalCount', 0):break
            time.sleep(0.5)
    return companies


def workable_search(tracks, terms=TERMS, pages=10):
    companies = {}
    for term in terms:
        token = None
        for _ in range(pages):
            query = {'query':term, 'location':'Brazil'}
            if token:query['pageToken'] = token
            data = fetch('https://jobs.workable.com/api/v1/jobs?'+urlencode(query))
            for job in data.get('jobs', []):
                if matches_track(job.get('title', ''), tracks):
                    company = job.get('company') or {}
                    row = companies.setdefault(company.get('title', ''), {'source':'workable_search', 'titles':[]})
                    row['titles'].append(job['title'])
                    website = re.sub(r'^https?://(?:www\.)?|/.*$', '', company.get('website') or '')
                    if website:row['website'] = website
            token = data.get('nextPageToken')
            if not token:break
            time.sleep(0.5)
    return companies


def getonbrd(tracks, terms=TERMS):
    companies = {}
    for term in terms:
        data = fetch('https://www.getonbrd.com/api/v0/search/jobs?'+urlencode({'query':term, 'remote':'true', 'per_page':100, 'expand':'["company"]'}))
        for job in data.get('data', []):
            attributes = job.get('attributes', {})
            countries = ' '.join(attributes.get('countries') or [])
            if not matches_track(attributes.get('title', ''), tracks) or not re.search(r'remote|brazil|brasil|latam', countries, re.I):continue
            company = (attributes.get('company') or {}).get('data') or {}
            name = (company.get('attributes') or {}).get('name') or str(company.get('id', '')).replace('-', ' ')
            if name:companies.setdefault(name, {'source':'getonbrd', 'titles':[]})['titles'].append(attributes['title'])
    return companies


def gupy_portal(tracks, terms=TERMS+('engenheiro de software', 'programador', 'analista de sistemas'), pages=10):
    """portal.gupy.io search pages: companies (career subdomains) with remote openings on the candidate's tracks."""
    from .external_catalog import next_data
    links = {}
    for term in terms:
        for page in range(1, pages+1):
            query = urlencode({'term':term, 'workplaceTypes[]':'remote', 'page':page}, safe='[]')
            listing = next_data('https://portal.gupy.io/job-search/'+query)['initialJobList']
            for job in listing.get('data', []):
                host = urllib.request.urlparse(job.get('jobUrl', '')).hostname or ''
                if matches_track(job.get('name', ''), tracks) and host.endswith('.gupy.io'):
                    links.setdefault('https://'+host+'/', {'source':'gupy_portal', 'titles':[]})['titles'].append(job['name'].strip())
            pagination = listing.get('pagination', {})
            if pagination.get('offset', 0)+pagination.get('limit', 12) >= pagination.get('total', 0):break
            time.sleep(0.3)
    return links


def linkedin_links(data):
    """Destinations of LinkedIn postings whose Apply leaves LinkedIn (collected with apply_mode=external)."""
    path = Path(data)/'jobs.json'
    jobs = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    links = {}
    for job in jobs.values():
        if job.get('source') == 'linkedin' and job.get('external_url'):
            for url in LINK.findall(job['external_url']):
                links.setdefault(url, {'source':'linkedin_external', 'titles':[]})['titles'].append(job.get('title', ''))
    return links


def github_links(repos=GITHUB_REPOS, pages=3):
    links = {}
    for repo in repos:
        for page in range(1, pages+1):
            issues = fetch(f'https://api.github.com/repos/{repo}/issues?'+urlencode({'state':'open', 'per_page':100, 'page':page}))
            for issue in issues:
                if 'pull_request' in issue:continue
                for url in LINK.findall(issue.get('body') or ''):
                    links.setdefault(url, {'source':'github:'+repo, 'titles':[]})['titles'].append(issue['title'])
            if len(issues) < 100:break
    return links


def candidates(companies, links, known):
    """Official boards to probe: direct links first, then name-derived slugs on each probed ATS."""
    found = {}
    def add(spec, origin):
        key = (spec['platform'], spec['board'].lower())
        if key in known:return
        row = found.setdefault(key, dict(spec, sources=[], titles=[]))
        if origin['source'] not in row['sources']:row['sources'].append(origin['source'])
        row['titles'] = list(dict.fromkeys(row['titles']+origin['titles']))[:5]
    for url, origin in links.items():
        try:add(board_spec(url), origin)
        except (SafetyError, ValueError):pass
    for name, origin in companies.items():
        names = slugs(name)+(slugs(origin['website'].split('.')[0]) if origin.get('website') else [])
        for slug in dict.fromkeys(names):
            for platform in PROBED:add({'platform':platform, 'board':slug}, origin)
    return list(found.values())


def discover_sources(data, register=False, sources=('linkedin', 'gupy', 'github', 'himalayas', 'workable', 'getonbrd'), workers=6, limit=None):
    data = Path(data);tracks = load_job_tracks(data);book = AnswerBook(data)
    saved = registry(data)
    known = {(b['platform'], b['board'].lower()) for b in saved['boards']+saved.get('excluded', [])}
    companies = {};links = {};errors = {}
    for name, collect in (('himalayas', himalayas), ('workable', workable_search), ('getonbrd', getonbrd)):
        if name not in sources:continue
        try:
            for company, origin in collect(tracks).items():
                if company:companies.setdefault(company, origin)
        except Exception as error:errors[name] = type(error).__name__+': '+str(error)[:200]
    for name, collect in (('linkedin', lambda: linkedin_links(data)), ('github', github_links), ('gupy', lambda: gupy_portal(tracks))):
        if name not in sources:continue
        try:
            for url, origin in collect().items():links.setdefault(url, origin)
        except Exception as error:errors[name] = type(error).__name__+': '+str(error)[:200]
    rows = candidates(companies, links, known)
    if limit:rows = rows[:limit]
    jobs_path = data/'jobs.json'
    seen = {job_key(j['url']) for j in json.loads(jobs_path.read_text(encoding='utf-8')).values()} if jobs_path.exists() else set()
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(validate_board, data, {'platform':r['platform'], 'board':r['board']}):r for r in rows}
        for future in as_completed(futures):
            row = futures[future]
            try:result = future.result()
            except Exception as error:result = {'error':type(error).__name__, 'checked_at':now()}
            if 'error' in result:continue
            evaluated = evaluate_board({'platform':row['platform'], 'board':row['board']}, result, tracks, book.facts, seen)
            if evaluated['validated']:results.append(dict(evaluated, sources=row['sources'], listing_titles=row['titles']))
    results.sort(key=lambda r:(-r.get('new_eligible_jobs', 0), -r.get('new_matching_jobs', 0)))
    chosen = [r for r in results if r.get('new_eligible_jobs', 0) > 0]
    added = add_boards(data, [r['platform']+':'+r['board'] for r in chosen])['added'] if register and chosen else []
    report = {'checked_at':now(), 'companies':len(companies), 'direct_links':len(links), 'boards_probed':len(rows),
              'boards_validated':len(results), 'boards_with_new_eligible':len(chosen),
              'new_eligible_jobs':sum(r['new_eligible_jobs'] for r in chosen), 'registered':added, 'errors':errors, 'boards':results}
    path = data/'reports'/('job-sources-'+time.strftime('%Y%m%d-%H%M%S')+'.json');write_json(path, report)
    report['report_path'] = str(path)
    return report
