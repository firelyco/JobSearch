"""Minimal provider check: no career evidence or job text is transmitted."""
import argparse,json,os,sys,time
from src import fit,llm_client

def main(argv=()):
    parser=argparse.ArgumentParser();parser.add_argument('--schema-probe',action='store_true');args=parser.parse_args(argv)
    rules=fit.load_json(fit.FIT_RULES_FILE,{})
    model=rules.get('evidence_review_model') or fit._fit_model()
    started=time.monotonic()
    try:
        schema={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False} if args.schema_probe or (os.environ.get('LLM_PROVIDER')=='nvidia' and os.environ.get('NVIDIA_FIT_JSON_SCHEMA')=='true') else None
        response=llm_client.call(llm_client.build_client(),model=model,system='Reply with JSON containing ok=true.' if schema else 'Reply OK.',user='Check API connectivity.',max_tokens=256,temperature=0.0,response_schema=schema)
        if schema and json.loads(response.text)!={'ok':True}:raise RuntimeError('Schema probe did not return the required object')
        if response.finish_reason=='length':raise RuntimeError('Provider health response was truncated')
        if not response.text.strip():raise RuntimeError('Provider returned no usable text')
        print(json.dumps({'model':model,'status':'ok','seconds':round(time.monotonic()-started,2),'input_tokens':response.input_tokens,'output_tokens':response.output_tokens,'reasoning_tokens':response.reasoning_tokens,'finish_reason':response.finish_reason,'schema_probe':bool(schema)}))
        return 0
    except Exception as error:
        message=str(error)
        for key in ('ANTHROPIC_API_KEY','NVIDIA_API_KEY','HISTORY_SCORER_TOKEN'):
            value=os.environ.get(key)
            if value:message=message.replace(value,'[redacted]')
        print(json.dumps({'model':model,'status':'error','type':type(error).__name__,'http_status':getattr(error,'status_code',None),'message':message[:1600]}))
        return 1

if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
