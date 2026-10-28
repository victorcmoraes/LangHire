"""Local title filters shared by discovery, triage and batch requalification."""
import json
from pathlib import Path
import re

from .application_safety import SafetyError
from .easy_answers import normalized


DEFAULT_JOB_TRACKS={
    'include':r'back.?end|software (?:engineer|developer)|desenvolv|developer|data engineer|engenheir.*dados|analytics engineer|(?:ai|ia|ml|tooling|integration|gtm) engineer|automa[ct]|integrac',
    'exclude':r'\b(?:staff|principal|manager|diretor|director|head|architect|arquiteto|master(?!\s+data)|lead|leader|qa|quality|tester|ios|android|mobile|abap)\b',
    'allow_mobile':False,
    'allow_pure_frontend':False,
}


def load_job_tracks(data=None):
    path=Path(data)/'settings.json' if data is not None else None
    settings=json.loads(path.read_text(encoding='utf-8')) if path and path.exists() else {}
    config=settings.get('job_tracks',{})
    if not isinstance(config,dict) or set(config)-set(DEFAULT_JOB_TRACKS):
        raise SafetyError('Invalid job_tracks configuration')
    rules=DEFAULT_JOB_TRACKS|config
    for key in ('allow_mobile','allow_pure_frontend'):
        if type(rules[key]) is not bool:raise SafetyError('job_tracks.'+key+' must be boolean')
    for key in ('include','exclude'):
        if not isinstance(rules[key],str) or not rules[key].strip():
            raise SafetyError('job_tracks.'+key+' must be a nonempty regular expression')
        try:rules[key]=re.compile(rules[key],re.I)
        except re.error as error:raise SafetyError('Invalid job_tracks.'+key+' regular expression') from error
    return rules


def matches_track(title,rules):
    return bool(rules['include'].search(normalized(title)))
