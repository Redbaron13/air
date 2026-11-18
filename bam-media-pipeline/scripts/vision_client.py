"""Explicit local or free OpenRouter JSON calls. No paid fallback."""
import base64
from contextlib import closing
import json
import math
import mimetypes
import os
import sqlite3
import time
from pathlib import Path
from urllib.request import Request, urlopen


def validate_output(value):
    if not isinstance(value,dict) or not value: raise ValueError('Model returned no JSON object')
    if 'agreementScore' in value:
        score=value['agreementScore']
        if isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score) or not 0<=score<=1:
            raise ValueError('Agreement score must be a finite number from zero to one')
    return value


def validate_catalog_entry(entry, image=False):
    pricing=entry.get('pricing',{}) if entry else {}
    if not entry or not entry.get('id','').endswith(':free') or not {'prompt','completion'} <= pricing.keys():
        raise ValueError('Explicit free model with verified pricing required')
    try:
        if any(not math.isfinite(float(v)) or float(v)!=0 for v in pricing.values()):
            raise ValueError('Every advertised price must be zero')
    except (TypeError,ValueError):
        raise ValueError('Every advertised price must be zero') from None
    if 'response_format' not in entry.get('supported_parameters',[]):
        raise ValueError('JSON response format unsupported')
    if image and 'image' not in entry.get('architecture',{}).get('input_modalities',[]):
        raise ValueError('Selected model does not accept images')


def reserve_request(path=None, now=None):
    """Atomic persistent budget. Failed attempts count; concurrent workers share this file."""
    now=time.time() if now is None else now
    limit=int(os.environ.get('OPENROUTER_MAX_REQUESTS_PER_DAY','10'))
    interval=float(os.environ.get('OPENROUTER_MIN_INTERVAL_SECONDS','6'))
    if not 1<=limit<=50 or not math.isfinite(interval) or interval<3:
        raise ValueError('Use 1–50 requests/day and at least 3 seconds between requests')
    path=Path(path or os.environ.get('OPENROUTER_BUDGET_PATH','/storage/active/openrouter-budget.sqlite3'))
    path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path,timeout=10,isolation_level=None)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS budget(day INTEGER PRIMARY KEY,count INTEGER,last REAL)')
        db.execute('BEGIN IMMEDIATE')
        day=int(now//86400)
        row=db.execute('SELECT count,last FROM budget WHERE day=?',(day,)).fetchone()
        latest=db.execute('SELECT MAX(last) FROM budget').fetchone()[0]
        if row and row[0]>=limit:raise ValueError('Daily free-model request budget exhausted')
        if latest is not None and now-latest<interval:raise ValueError('Request pacing limit; retry later')
        db.execute('INSERT INTO budget VALUES(?,1,?) ON CONFLICT(day) DO UPDATE SET count=count+1,last=excluded.last',(day,now))
        db.execute('COMMIT')
    path.chmod(0o600)


def call_free_model(prompt,image_path=None):
    model=os.environ.get('OPENROUTER_MODEL','')
    key=os.environ.get('OPENROUTER_API_KEY','')
    if not model.endswith(':free') or not key:
        raise ValueError('Set OPENROUTER_API_KEY and an explicitly selected :free model')
    # Verify current catalog capabilities and pricing rather than assuming a model stays free.
    with urlopen('https://openrouter.ai/api/v1/models',timeout=30) as r: catalog=json.load(r)['data']
    entry=next((m for m in catalog if m['id']==model),None)
    validate_catalog_entry(entry,bool(image_path))
    content=[{'type':'text','text':prompt}]
    if image_path:
        if 'image' not in entry.get('architecture',{}).get('input_modalities',[]):
            raise ValueError('Selected model does not accept images')
        p=Path(image_path)
        mime=mimetypes.guess_type(p.name)[0]
        if mime not in ('image/jpeg','image/png','image/webp') or p.stat().st_size>10*1024*1024:
            raise ValueError('Use a JPEG/PNG/WebP preview below 10 MiB')
        content.append({'type':'image_url','image_url':{'url':f'data:{mime};base64,'+base64.b64encode(p.read_bytes()).decode()}})
    body={'model':model,'messages':[{'role':'user','content':content}], 'max_tokens':1600,
          'response_format':{'type':'json_object'}, 'temperature':0,
          'provider':{'allow_fallbacks':False,'max_price':{'prompt':0,'completion':0}}}
    req=Request('https://openrouter.ai/api/v1/chat/completions',data=json.dumps(body).encode(),
                headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    reserve_request()
    with urlopen(req,timeout=120) as r: response=json.load(r)
    if response.get('model') != model: raise ValueError('Returned model identity mismatch')
    # Reject prose, fenced JSON and truncated responses; never repair an answer into approval.
    choice=response['choices'][0]
    if choice.get('finish_reason')!='stop': raise ValueError('Model response incomplete')
    return validate_output(json.loads(choice['message']['content']))
