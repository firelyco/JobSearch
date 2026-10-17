"""Resumable execution of the existing evidence rubric; checkpoints stay private."""
from __future__ import annotations
import copy
import json
import logging
import os
import time
from src import evidence_fit as core, llm_client

log = logging.getLogger(__name__)
PIPELINE_VERSION = 'resume-v1'
COMPACT = '\nKeep JSON compact. Explanations: at most 12 words. Use short exact contiguous quotes sufficient to establish each requirement. Never omit distinct required capabilities to shorten output. Do not repeat evidence text.'


def object_schema(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


STRING={'type':'string'}
BOOL={'type':'boolean'}
STRINGS={'type':'array','items':STRING}
REQUIREMENT=object_schema({'id':STRING,'quote':STRING,'category':{'enum':sorted(core.CATEGORIES),'type':'string'},
    'critical':BOOL,'required':BOOL,'match':{'enum':sorted(core.MATCHES),'type':'string'},
    'evidence_ids':STRINGS,'explanation':STRING,'hard_gate':BOOL,'exclusion_rule_key':STRING,'exclusion_quote':STRING})
EDGE=object_schema({'kind':STRING,'evidence_ids':STRINGS,'requirement_ids':STRINGS})
SCOPE=object_schema({'kind':STRING,'quote':STRING})
LOCATION=object_schema({'status':STRING,'quote':STRING,'reason':STRING})
EXTRACTION_SCHEMA=object_schema({'requirements':{'type':'array','items':REQUIREMENT},
    'differentiators':{'type':'array','items':EDGE},'scope':SCOPE,'location':LOCATION,'coverage_complete':BOOL})
CHECK=object_schema({'id':STRING,'category':{'enum':sorted(core.CATEGORIES),'type':'string'},
    'match':{'enum':sorted(core.MATCHES),'type':'string'},'critical':BOOL,'required':BOOL,
    'hard_gate_supported':BOOL,'evidence_ids':STRINGS,'exclusion_rule_key':STRING,'exclusion_quote':STRING,'explanation':STRING})
REVIEW_SCHEMA=object_schema({'coverage_complete':BOOL,'missing_critical_requirements':STRINGS,
    'requirements':{'type':'array','items':CHECK},'accepted_differentiators':STRINGS,
    'accepted_differentiator_evidence':{'type':'array','items':EDGE},'scope_supported':BOOL,
    'scope_kind':STRING,'scope_quote':STRING,'location_supported':BOOL,'location':LOCATION})


def decode(text):
    """Never complete a truncated response by inventing missing JSON."""
    value=json.loads(text[text.find('{'):text.rfind('}')+1])
    if not isinstance(value,dict):raise ValueError('Expected an object')
    return value


def validate_proposal(value):
    if not isinstance(value,dict) or type(value.get('coverage_complete')) is not bool:
        raise ValueError('Invalid extraction coverage')
    rows=value.get('requirements')
    if not isinstance(rows,list) or not rows:raise ValueError('No requirements')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or row['id'] in seen:
            raise ValueError('Invalid requirement identity')
        seen.add(row['id'])
        if not isinstance(row.get('quote'),str) or row.get('category') not in core.CATEGORIES:
            raise ValueError('Invalid requirement structure')
        if type(row.get('critical')) is not bool or type(row.get('required')) is not bool:
            raise ValueError('Invalid requirement flags')
        if not isinstance(row.get('evidence_ids',[]),list):raise ValueError('Invalid evidence IDs')
    if not isinstance(value.get('scope'),dict) or not isinstance(value.get('location'),dict):
        raise ValueError('Invalid scope or location')


def merge_repair(original,delta,allowed_ids,missing_quotes):
    """Only replace broken rows or add cited omitted requirements; retain all others."""
    validate_proposal(delta)
    result=copy.deepcopy(original)
    rows={row['id']:row for row in result['requirements']}
    normalized=lambda value:' '.join(value.casefold().split())
    for row in delta['requirements']:
        rid=row['id']
        if rid in rows:
            if rid not in allowed_ids:raise ValueError('Repair changed an intact requirement')
        elif not any(normalized(q) in normalized(row['quote']) or normalized(row['quote']) in normalized(q) for q in missing_quotes):
            raise ValueError('Repair added an unrequested requirement')
        rows[rid]=row
    result['requirements']=list(rows.values())
    # Repairs can correct role facts, but never remove previously proposed edges.
    result['scope']=delta['scope'];result['location']=delta['location']
    edges=result.setdefault('differentiators',[])
    for edge in delta.get('differentiators',[]):
        if edge not in edges:edges.append(edge)
    validate_proposal(result)
    return result


def assess(profile,job,jd_text,*,client,model,rules,resume=None,checkpoint=None):
    from src.fit_scorer import salary_text,verified_base_comp_status
    started=time.monotonic()
    inventory=core.build_inventory(profile,rules);jd=salary_text(jd_text)
    fingerprint=core.input_hash(profile,job,jd_text,rules,model)
    from src.compensation import parse_salary
    comp=parse_salary(jd_text,job,float(rules.get('comp_floor_usd',0)))['status']
    job_facts={k:job.get(k) for k in ('title','company','location','url')}
    state={'pipeline_version':PIPELINE_VERSION,'input_hash':fingerprint,'inventory':inventory,
           'job':job_facts,'model':model,'review_model':rules.get('evidence_review_model') or model,
           'stage':'extraction','metrics':[]}
    if (isinstance(resume,dict) and resume.get('pipeline_version')==PIPELINE_VERSION
            and resume.get('input_hash')==fingerprint and resume.get('model')==model
            and resume.get('review_model')==state['review_model']):
        candidate=copy.deepcopy(resume)
        try:
            if candidate.get('stage')!='extraction':validate_proposal(candidate.get('extraction'))
            state=candidate
        except (ValueError,TypeError):pass
    resumed_stage=state['stage'];metrics_start=len(state.get('metrics',[]))
    def save():
        if checkpoint:checkpoint(copy.deepcopy(state))
    def request(phase,prompt,user,schema,budget):
        before=time.monotonic();metric={'stage':phase}
        try:
            response=llm_client.call(client,model=state['review_model'] if phase in {'review','repair_review'} else model,
                system=prompt+COMPACT+'\nPRIVATE EVIDENCE AND RULES:\n'+core.canonical({'inventory':inventory,'rules':rules}),
                user=core.canonical(user),max_tokens=budget,temperature=0.0,cache_system=True,structured_json=True,
                response_schema=schema if os.environ.get('NVIDIA_FIT_JSON_SCHEMA')=='true' else None)
            metric.update(input_tokens=response.input_tokens,output_tokens=response.output_tokens,
                          reasoning_tokens=response.reasoning_tokens,finish_reason=response.finish_reason)
            if response.finish_reason=='length':
                metric['error']='truncated_output'
                raise ValueError('Truncated output')
            return decode(response.text)
        except Exception as error:
            metric.setdefault('error',type(error).__name__)
            raise
        finally:
            metric['seconds']=round(time.monotonic()-before,3)
            state.setdefault('metrics',[]).append(metric)
            log.info('Evidence stage=%s seconds=%.3f output_tokens=%s finish=%s error=%s',phase,
                     metric['seconds'],metric.get('output_tokens',0),metric.get('finish_reason',''),metric.get('error',''))
    extraction=state.get('extraction',{});review=state.get('review',{})
    failure=None
    if len(jd)<200 or len(jd)>70000 or not inventory:
        result=core.review_result({}, {},inventory,jd,job,comp,rules)
        result.private['validation_errors'].append('Missing inventory, missing JD, or JD exceeds safe assessment length')
    else:
        try:
            while state['stage']!='complete':
                phase=state['stage']
                if phase=='extraction':
                    extraction=request(phase,core.EXTRACT_PROMPT,{'job':job_facts,'job_text':jd},EXTRACTION_SCHEMA,8000)
                    validate_proposal(extraction)
                    state.update(extraction=extraction,stage='review');save()
                elif phase in {'review','repair_review'}:
                    review=request(phase,core.REVIEW_PROMPT,{'job_text':jd,'job':job_facts,'proposal':extraction},REVIEW_SCHEMA,6000)
                    if not isinstance(review.get('requirements'),list) or type(review.get('coverage_complete')) is not bool:
                        raise ValueError('Invalid review structure')
                    state.update(review=review,stage='decision');save()
                elif phase=='decision':
                    result=core.review_result(extraction,review,inventory,jd,job,comp,rules)
                    repairable=result.private['missing_critical_requirements'] or result.private['validation_errors']
                    if result.recommendation=='review' and repairable and not state.get('repair_attempted'):
                        state.update(stage='repair',repair_errors=result.private['validation_errors'],
                                     missing_requirements=result.private['missing_critical_requirements'])
                    else:state['stage']='complete'
                    save()
                elif phase=='repair':
                    # Scope/location errors are rechecked by the independent reviewer. Include
                    # broken citation IDs only; do not regenerate all the correctly extracted rows.
                    normalize=lambda value:' '.join(str(value).casefold().split())
                    broken=[r['id'] for r in extraction['requirements'] if normalize(r['quote']) not in normalize(jd)]
                    missing=[q for q in state.get('missing_requirements',[]) if isinstance(q,str) and normalize(q) in normalize(jd)]
                    if not broken and not missing:
                        state.update(repair_attempted=True,stage='repair_review');save();continue
                    delta=request(phase,core.EXTRACT_PROMPT,{'job':job_facts,'job_text':jd,
                        'repair_instructions':'Return the extraction JSON shape, but ONLY replacement rows for broken IDs and new rows covering the exact omitted requirements. Keep all other rows out of requirements; they are retained by code. Preserve broken row IDs; give new requirements unique IDs. Copy the original scope and location unless correcting them.',
                        'broken_requirements':[r for r in extraction['requirements'] if r['id'] in broken],
                        'omitted_requirements':missing,'existing_ids':[r['id'] for r in extraction['requirements']],
                        'scope':extraction['scope'],'location':extraction['location']},EXTRACTION_SCHEMA,8000)
                    extraction=merge_repair(extraction,delta,set(broken),missing)
                    state.update(extraction=extraction,repair_attempted=True,stage='repair_review');save()
                else:raise ValueError('Invalid checkpoint stage')
            result=core.review_result(extraction,review,inventory,jd,job,comp,rules)
        except (ValueError,TypeError,AttributeError,KeyError) as error:
            failure={'phase':state['stage'],'type':type(error).__name__}
            result=core.review_result({}, {},inventory,jd,job,comp,rules)
            result.private['validation_errors'].append('Invalid model structure in '+state['stage'])
            result.private['model_error']=failure
            save()
        except Exception:
            # Network/storage failure preserves the last completed stage, then fails safely.
            save()
            raise
    result.public.update(input_hash=fingerprint)
    result.private.update(job_text=jd,input_hash=fingerprint,engine_version=core.ENGINE_VERSION,model=model,
        review_model=state['review_model'],job=job_facts,rules_version=rules.get('fit_policy_version'),
        repair_attempted=bool(state.get('repair_attempted')),
        execution={'pipeline':PIPELINE_VERSION,'resumed_from':resumed_stage,'stage':state['stage'],
            'seconds':round(time.monotonic()-started,3),'calls_this_attempt':len(state.get('metrics',[]))-metrics_start,
            'metrics':state.get('metrics',[])})
    return result
