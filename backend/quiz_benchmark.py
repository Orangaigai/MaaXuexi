"""Small labeled quiz evaluations, independent of Android and daily task state."""
import hashlib
import json
import math
import statistics
import time
from pathlib import Path
import numpy as np
from PIL import Image


def load_cases(path):
    path=Path(path).resolve()
    data=json.loads(path.read_text('utf-8-sig'))
    cases=data.get('cases',[])
    if not 1 <= len(cases) <= 40:
        raise ValueError('Expected 1-40 labeled cases')
    ids=set()
    for case in cases:
        options=case.get('options',{})
        expected=case.get('expected',[])
        if (not isinstance(case.get('id'),str) or case['id'] in ids
                or case.get('kind') not in ('daily','timed')
                or not isinstance(case.get('question'),str) or len(case['question'])<5
                or not isinstance(options,dict) or not 2<=len(options)<=6
                or list(options)!=list('ABCDEF'[:len(options)])
                or any(not isinstance(v,str) or not v.strip() for v in options.values())
                or not isinstance(expected,list) or not expected
                or len(expected)!=len(set(expected)) or not set(expected).issubset(options)
                or case.get('question_type') not in ('单选题','多选题')
                or (case['question_type']=='单选题' and len(expected)!=1)
                or case.get('deadline_seconds',0)<=0):
            raise ValueError('Invalid labeled case')
        if case['kind']=='daily' and not case.get('hint'):
            raise ValueError('Daily text evaluation requires the full app hint')
        if case['kind']=='timed' and (case['question_type']!='单选题' or len(options)>4):
            raise ValueError('Timed evaluation supports current 2-4 choice races')
        evidence=case.get('evidence',{})
        if evidence.get('kind') not in ('app_hint','platform_feedback') or not evidence.get('description'):
            raise ValueError('A model answer is not a ground-truth label')
        for item in case.get('images',[])+case.get('evidence',{}).get('images',[]):
            file=(path.parent/item['file']).resolve()
            if (not file.is_relative_to(path.parent) or not file.is_file()
                    or hashlib.sha256(file.read_bytes()).hexdigest()!=item['sha256']):
                raise ValueError('Evidence image missing, changed or outside case directory')
        if not case.get('images'):
            raise ValueError('Paired routes require pre-answer screenshots')
        ids.add(case['id'])
    return cases


def estimate_cost(usage,prices):
    if not prices or not isinstance(usage,dict):return None
    prompt,completion=usage.get('prompt_tokens'),usage.get('completion_tokens')
    hit=usage.get('prompt_cache_hit_tokens',0)
    if any(type(v) is not int or v<0 for v in (prompt,completion,hit)) or hit>prompt:return None
    return round(((prompt-hit)*prices['input']+hit*prices['cached_input']+completion*prices['output'])/1_000_000,8)


def evaluate(resolver,case,route,folder,prices=None):
    started=time.monotonic()
    images=[np.ascontiguousarray(np.asarray(Image.open(Path(folder)/v['file']).convert('RGB'))[:,:,::-1])
            for v in case['images']] if route=='image' else []
    if case['kind']=='daily':
        payload={k:case[k] for k in ('question_type','question','options','hint')}
        answer=resolver.resolve_choice(images,case['question_type'],
                                       **({'text_input':payload} if route=='text' else {}))
    else:
        index=resolver.resolve_race_choice(images,list(case['options'].values()),case['question'],
                                          visual_required=(route=='image'))
        answer=None if index is None else [list(case['options'])[index]]
    seconds=time.monotonic()-started
    correct=answer is not None and sorted(answer)==sorted(case['expected'])
    usage=resolver.last_usage
    return dict(case_id=case['id'],kind=case['kind'],route=route,answer=answer,expected=case['expected'],
        correct=correct,seconds=round(seconds,4),deadline_seconds=case['deadline_seconds'],
        correct_in_time=correct and seconds<=case['deadline_seconds'],
        input_mode=resolver.last_input_mode,response_model=resolver.last_response_model,
        usage=usage,estimated_cost=estimate_cost(usage,prices),
        error=resolver.last_error,http_status=resolver.last_http_status,attempts=resolver.last_request_attempts)


def summarize(rows):
    if not rows:return dict(total=0)
    durations=sorted(r['seconds'] for r in rows)
    costs=[r['estimated_cost'] for r in rows]
    return dict(total=len(rows),correct=sum(r['correct'] for r in rows),
        correct_in_time=sum(r['correct_in_time'] for r in rows),
        failures=sum(r['answer'] is None for r in rows),
        median_seconds=round(statistics.median(durations),4),
        p95_seconds=durations[math.ceil(.95*len(durations))-1],maximum_seconds=max(durations),
        estimated_cost=sum(costs) if all(v is not None for v in costs) else None)
