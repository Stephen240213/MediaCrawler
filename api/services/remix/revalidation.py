"""Local-only recovery of a saved model response. This module never calls a provider."""
import hashlib
import json

from . import artifacts, providers, store, task_contracts


def evidence_hash(evidence):
    return hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()


def restore(job_id):
    job=store.get(job_id)
    if not job:raise ValueError('任务不存在')
    if job['status']=='completed':return job
    if job['status'] not in ('failed','cancelled','interrupted'):
        raise ValueError('任务仍在执行，不能进行本机重新校验')
    path=store.root()/'jobs'/job_id/'rejected-report.json'
    if not path.is_file():raise ValueError('此历史任务没有保存模型响应，无法离线恢复')
    saved=json.loads(path.read_text())
    expected=evidence_hash(job.get('evidence',[]))
    if saved.get('evidence_sha256')!=expected:
        raise ValueError('模型响应与当前来源版本不一致，已拒绝拼接不同版本')
    report=providers.validate_report(saved['candidate'],job['evidence'],require_creation=task_contracts.mode(job['evidence'])!='analysis')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT body FROM jobs WHERE id=?',(job_id,)).fetchone()
        current=json.loads(row[0])
        if current['status']=='completed':return current
        if current['status'] not in ('failed','cancelled','interrupted') or evidence_hash(current.get('evidence',[]))!=expected:
            raise ValueError('任务状态或来源已变化，请刷新后重试本机校验')
        current.update(status='completed',stage='completed',report=report,error=None,model_diagnostics=None,
            model=saved.get('model'),prompt_version=saved.get('prompt_version'),usage=saved.get('usage',{}),
            recovered_response=True,model_generated_at=saved.get('created_at'),finished_at=store.now(),updated_at=store.now(),
            evidence_sha256=expected,cache_hit=False,artifacts=artifacts.manifest(report,current['evidence'],True))
        db.execute('UPDATE jobs SET status=?,stage=?,body=? WHERE id=?',('completed','completed',json.dumps(current,ensure_ascii=False),job_id))
    return current
