"""Explicit local or free OpenRouter JSON calls. No paid fallback."""
import base64
import json
import math
import mimetypes
import os
from pathlib import Path
from urllib.request import Request, urlopen


def validate_output(value):
    if not isinstance(value,dict) or not value: raise ValueError('Model returned no JSON object')
    if 'agreementScore' in value:
        score=value['agreementScore']
        if isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score) or not 0<=score<=1:
            raise ValueError('Agreement score must be a finite number from zero to one')
    return value


def call_free_model(prompt,image_path=None):
    model=os.environ.get('OPENROUTER_MODEL','')
    key=os.environ.get('OPENROUTER_API_KEY','')
    if not model.endswith(':free') or not key:
        raise ValueError('Set OPENROUTER_API_KEY and an explicitly selected :free model')
    # Verify current catalog capabilities and pricing rather than assuming a model stays free.
    with urlopen('https://openrouter.ai/api/v1/models',timeout=30) as r: catalog=json.load(r)['data']
    entry=next((m for m in catalog if m['id']==model),None)
    if not entry or any(float(entry.get('pricing',{}).get(k,'-1'))!=0 for k in ('prompt','completion')):
        raise ValueError('Selected model is unavailable or is not free')
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
          'provider':{'allow_fallbacks':False,'max_price':{'prompt':0,'completion':0}}}
    req=Request('https://openrouter.ai/api/v1/chat/completions',data=json.dumps(body).encode(),
                headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urlopen(req,timeout=120) as r: response=json.load(r)
    # Reject prose, fenced JSON and truncated responses; never repair an answer into approval.
    choice=response['choices'][0]
    if choice.get('finish_reason')!='stop': raise ValueError('Model response incomplete')
    return validate_output(json.loads(choice['message']['content']))
