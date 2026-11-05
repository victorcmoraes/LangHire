"""Lever bookmarklet helpers; the server and the queue live in apply_assist (the Apply Assist extension).

Lever answered HTTP 400 ("error verifying your application") to submissions from the automated
browser even after a human CAPTCHA, so nothing here drives a browser: a bookmarklet asks this
local server for answers, fills the page, and reports the page the portal shows afterwards."""
import secrets
from pathlib import Path

from .external_apply import FIELDS

PORT = 8799


def token(data):
    path = Path(data)/'lever-assist-token'
    if not path.exists():path.write_text(secrets.token_hex(24), encoding='utf-8')
    return path.read_text(encoding='utf-8').strip()


def script(secret):
    return (Path(__file__).with_name('lever_assist.js').read_text(encoding='utf-8')
            .replace('__TOKEN__', secret).replace('__FIELDS__', FIELDS).replace('__PORT__', str(PORT)))


def bookmarklet():
    return ("javascript:(()=>{const s=document.createElement('script');"
            f"s.src='http://127.0.0.1:{PORT}/assist.js?'+Date.now();document.head.append(s)}})()")
