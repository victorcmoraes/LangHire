"""Strict identities for public Cornerstone career sites."""
import re
from urllib.parse import parse_qs,urlsplit


TENANT=r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
SITE=r'[1-9][0-9]{0,9}'


def board_parts(board):
    match=re.fullmatch('('+TENANT+')(?:/('+SITE+'))?',board if isinstance(board,str) else '')
    if not match:raise ValueError('Invalid Cornerstone board')
    return match[1],int(match[2] or 1)


def url_parts(url):
    p=urlsplit(url);host=p.hostname or ''
    if not host.endswith('.csod.com'):return None
    tenant=host.removesuffix('.csod.com')
    if p.scheme!='https' or p.port not in (None,443) or p.username or p.password or not re.fullmatch(TENANT,tenant):
        raise ValueError('Invalid Cornerstone host')
    match=re.fullmatch(r'/ux/ats/careersite/('+SITE+r')(?:/.*)?',p.path)
    if not match:raise ValueError('Invalid Cornerstone site')
    query=parse_qs(p.query,keep_blank_values=True)
    if 'c' in query and query['c']!=[tenant]:raise ValueError('Cornerstone tenant mismatch')
    return tenant,int(match[1])


def job_parts(url):
    parts=url_parts(url)
    if parts is None:return None
    match=re.fullmatch(r'/ux/ats/careersite/'+SITE+r'/home/requisition/('+SITE+r')/?',urlsplit(url).path)
    if not match:raise ValueError('Invalid Cornerstone requisition')
    return *parts,match[1]


def job_url(tenant,site,requisition):
    return f'https://{tenant}.csod.com/ux/ats/careersite/{site}/home/requisition/{requisition}?c={tenant}'
