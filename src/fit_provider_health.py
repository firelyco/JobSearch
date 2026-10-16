"""Minimal provider check: no career evidence or job text is transmitted."""
import json,os
from src import fit,llm_client

def main():
    rules=fit.load_json(fit.FIT_RULES_FILE,{})
    model=rules.get('evidence_review_model') or fit._fit_model()
    try:
        response=llm_client.call(llm_client.build_client(),model=model,system='Reply OK.',user='Check API connectivity. Reply OK.',max_tokens=32,temperature=0.0)
        print(json.dumps({'model':model,'status':'ok'}))
        return 0
    except Exception as error:
        message=str(error)
        for key in ('ANTHROPIC_API_KEY','NVIDIA_API_KEY','HISTORY_SCORER_TOKEN'):
            value=os.environ.get(key)
            if value:message=message.replace(value,'[redacted]')
        print(json.dumps({'model':model,'status':'error','type':type(error).__name__,'http_status':getattr(error,'status_code',None),'message':message[:1600]}))
        return 1

if __name__=='__main__':raise SystemExit(main())
