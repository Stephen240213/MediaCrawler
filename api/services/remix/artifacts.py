"""Assess sources, analysis and writing independently of the executor status."""
import json

from . import evidence_policy,task_contracts
from .schemas import Rewrite


def draft_candidates(candidate,evidence):
    if task_contracts.mode(evidence)=='analysis':return []
    valid={e['id'] for e in evidence};facts=evidence_policy.factual_ids(evidence)
    brief=next((json.loads(e['text']) for e in evidence if e['kind']=='brief'),{})
    execution=next((json.loads(e['text']) for e in evidence if e['kind']=='creation_execution'),{})
    result=[]
    rewrites=candidate.get('rewrites') if isinstance(candidate,dict) else None
    for i,raw in enumerate(rewrites if isinstance(rewrites,list) else []):
        try:rewrite=Rewrite.model_validate(raw)
        except ValueError:continue
        if not set(rewrite.evidence_ids)<=valid or not set(rewrite.evidence_ids)&facts:continue
        if brief.get('framework','auto')!='auto' and rewrite.framework!=brief['framework']:continue
        agent=execution.get('agent',{})
        if (agent.get('content_type')=='视频' or agent.get('id')=='script_writer') and (not rewrite.hook or not rewrite.script):continue
        result.append(dict(index=i,rewrite=rewrite.model_dump()))
    return result


def manifest(candidate,evidence,validated=False):
    ready=draft_candidates(candidate,evidence)
    coverage=next((json.loads(e['text']) for e in evidence if e['kind']=='coverage'),{})
    rewrites=candidate.get('rewrites') if isinstance(candidate,dict) else None
    mode=task_contracts.mode(evidence)
    return dict(
        sources=dict(state='ready' if evidence else 'pending',dataset=coverage.get('dataset')),
        analysis=dict(state=('reused' if any(e['kind'] in ('analysis_dependency','report_context') for e in evidence) else 'not_requested') if mode=='writing' else 'ready' if validated else 'review_required'),
        creation=dict(state='not_requested' if mode=='analysis' else 'ready' if ready else 'blocked',ready_count=len(ready),
            generated_count=len(rewrites) if isinstance(rewrites,list) else 0),
        validation=dict(state='ready' if validated else 'review_required',human_review='required'))
