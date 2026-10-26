"""Explicit contracts for new tasks; old combined jobs retain their original graph."""
import hashlib
import json


def mode(evidence):
    entry=next((e for e in evidence if e['kind']=='task_contract'),None)
    return json.loads(entry['text'])['kind'] if entry else 'legacy'


def result_hash(job):
    data={k:job.get(k) for k in ('report','evidence','source','transcript','duration')}
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()


def kind(job):
    return job.get('task_kind',job.get('input',{}).get('task_kind','legacy'))
