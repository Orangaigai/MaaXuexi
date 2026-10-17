"""Validate cases by default; --run makes bounded official DeepSeek requests.

No Android input, task execution, model switching or answer cache. Existing
encrypted credentials are sent only to the fixed official DeepSeek endpoint.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from quiz_benchmark import load_cases,evaluate,summarize
from answer_client import AIResolver
from provider_support import MODEL


class BenchmarkResolver(AIResolver):
    def _post(self,data,time_limit=None):
        # A sample is one paid request, never an automatic retry.
        return super()._post(data,time_limit=time_limit or 12)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,default=ROOT/'tests/fixtures/model-eval/cases.json')
    parser.add_argument('--models',nargs='+',default=[MODEL],help='Official DeepSeek model IDs; never changes the running model')
    parser.add_argument('--routes',nargs='+',choices=['text','image'],default=['text','image'])
    parser.add_argument('--repeats',type=int,default=1)
    parser.add_argument('--max-calls',type=int,default=16)
    parser.add_argument('--run',action='store_true',help='Enable paid model requests; otherwise only validate cases and print the plan')
    parser.add_argument('--output',type=Path,default=ROOT/'logs/model-comparison/latest.json')
    parser.add_argument('--input-price',type=float,help='Optional currency units per million uncached input tokens')
    parser.add_argument('--cached-input-price',type=float)
    parser.add_argument('--output-price',type=float)
    args=parser.parse_args()
    cases=load_cases(args.cases)
    total=len(cases)*len(args.models)*len(args.routes)*args.repeats
    if (not 1<=args.repeats<=5 or not 1<=args.max_calls<=64 or total>args.max_calls
            or len(set(args.models))!=len(args.models) or len(set(args.routes))!=len(args.routes)
            or any(not re.fullmatch(r'[\w.:-]{1,100}',model) for model in args.models)):
        parser.error('Invalid plan or too many calls; reduce cases/models/repeats')
    rates=(args.input_price,args.cached_input_price,args.output_price)
    if any(v is not None for v in rates) and not all(v is not None and v>=0 for v in rates):
        parser.error('Supply all three non-negative token prices, or omit all')
    prices=dict(zip(('input','cached_input','output'),rates)) if all(v is not None for v in rates) else None
    plan=dict(cases=len(cases),models=args.models,routes=args.routes,repeats=args.repeats,
              maximum_calls=total,paid_requests=args.run,no_android=True,no_task_execution=True)
    print(json.dumps(plan,ensure_ascii=False),flush=True)
    if not args.run:return 0
    from key_store import load_key
    # DPAPI store is relative in the installed app. Read only the chosen root.
    import os
    os.chdir(ROOT)
    key=load_key()
    if not key:raise RuntimeError('Official DeepSeek key unavailable')
    rows=[]
    result=dict(checked_at=datetime.now().astimezone().isoformat(),plan=plan,
                application_answer_cache=False,provider_prefix_cache='reported in usage',
                pricing=prices,cost_is_estimate=True,results=rows,summaries={})
    args.output=args.output.resolve();args.output.parent.mkdir(parents=True,exist_ok=True)
    try:
        for model in args.models:
            resolver=BenchmarkResolver(key,model)
            try:
                for repeat in range(args.repeats):
                    for index,case in enumerate(cases):
                        routes=args.routes if (repeat+index)%2==0 else list(reversed(args.routes))
                        for route in routes:
                            row=evaluate(resolver,case,route,args.cases.resolve().parent,prices)
                            row.update(model=model,repeat=repeat+1)
                            rows.append(row)
                            print(json.dumps({k:row[k] for k in ('case_id','model','route','correct','seconds','error')},ensure_ascii=False),flush=True)
                            if row['http_status'] in (401,402,403,429):
                                raise RuntimeError('Provider rejected the bounded benchmark; no automatic retry')
            finally:resolver.session.close()
    finally:
        for model in args.models:
            for route in args.routes:
                for kind in sorted({case['kind'] for case in cases}):
                    group=[r for r in rows if r['model']==model and r['route']==route and r['kind']==kind]
                    if group:result['summaries'][f'{model}/{route}/{kind}']=summarize(group)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps(result['summaries'],ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
