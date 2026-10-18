#!/usr/bin/env python3
"""Check the running local site using synthetic accounts and captured email."""
from pathlib import Path
import hashlib
from html.parser import HTMLParser
import http.cookiejar
import json
import re
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:8080'
class Page(HTMLParser):
    def __init__(self, raw):
        super().__init__(); self.inputs={}; self.links=[]; self.feed(raw)
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='input' and a.get('name'): self.inputs[a['name']]=a.get('value','')
        if tag=='a' and a.get('href'): self.links.append(a['href'])
def client():
    c = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    c.open(BASE+'/api/session').read()
    return c
def get(c, path):
    return c.open(urllib.parse.urljoin(BASE, path)).read().decode()
def post(c, path, data):
    return c.open(urllib.parse.urljoin(BASE, path), urllib.parse.urlencode(data).encode()).read().decode()
def check(ok, label):
    if not ok: raise RuntimeError(label)
    print('PASS:',label)
accounts=json.loads((ROOT/'var/local/accounts.json').read_text())
for email in accounts:
    c=client(); page=Page(get(c,'/signin'))
    raw=post(c,'/signin', {'post':page.inputs['post'],'email':email,'password':accounts[email]})
    check(email in raw and 'Sign out' in raw, 'HTTP login '+email)
    try:
        paper=get(c,'/paper/1')
    except urllib.error.HTTPError as e:
        if email != 'outsider@hotcrp.localhost' or e.code not in [403,404]: raise
        paper=e.read().decode()
    if email != 'outsider@hotcrp.localhost':
        check('Local HotCRP workflow test' in paper, 'paper access '+email)
    else:
        check('Local HotCRP workflow test' not in paper,'outsider denied paper')
    if email=='author@hotcrp.localhost':
        check('Synthetic review for local testing.' not in paper,'unreleased review hidden from author')
        links=Page(paper).links
        docs=[x for x in links if '/doc/' in x or x.startswith('doc/')]
        check(bool(docs),'PDF download link exists')
        pdf=c.open(urllib.parse.urljoin(BASE,docs[0])).read()
        check(hashlib.sha256(pdf).digest()==hashlib.sha256((ROOT/'test/sample-linearized.pdf').read_bytes()).digest(),'PDF download matches uploaded bytes')
    if email=='reviewer@hotcrp.localhost':
        check('Synthetic review for local testing.' in paper,'submitted review visible to reviewer')
# Request a reset email, without following the link or changing credentials.
c=client(); page=Page(get(c,'/forgotpassword'))
post(c,'/forgotpassword',{'post':page.inputs['post'],'email':'author@hotcrp.localhost'})
messages=json.load(urllib.request.urlopen('http://127.0.0.1:8025/api/v1/messages'))
check(any(any(x.get('Address')=='author@hotcrp.localhost' for x in m.get('To',[])) for m in messages.get('messages',[])),'password reset email captured locally')
# All HTTP routes pass through HotCRP; private files must never be served.
for path in ['/conf/options.php','/var/local/accounts.json','/.git/config','/local-dev/manage.py']:
    try:
        raw=get(client(),path)
    except urllib.error.HTTPError as e:
        check(e.code in [403,404], 'private path blocked '+path)
    else:
        check('dbPassword' not in raw and '[remote "origin"]' not in raw and 'Manage a project-local' not in raw and all(p not in raw for p in accounts.values()),'private path not served '+path)
