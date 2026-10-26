"""Typed evidence, independent drafts and deterministic recovery; no model traffic."""
import json

import pytest

from tests.test_content_remix import isolated, client, report
from tests.test_creation_sources_and_contract import creation_input
from api.services.remix import artifacts, providers, revalidation, store, workflow, topics
from api.services.remix.reference_evidence import prepare


def inputs():
    payload=creation_input()
    source=workflow.source_for(payload)
    evidence=workflow.build_evidence(source,[])
    evidence.append(dict(id='C9999',kind='source_excerpt',origin_kind='comment',text='示例评论',factual_source=False))
    candidate=report(evidence)
    candidate['comment_analysis']=[dict(observation='评论主题频次',interpretation='仅描述本次样本，不推断全体观众',evidence_ids=['A0001'])]
    return payload,source,evidence,candidate


@pytest.mark.parametrize('references,basis',[
    (['A0001'],'comment_statistics'),(['C9999'],'raw_comments'),(['A0001','C9999'],'mixed')])
def test_comment_evidence_is_typed_and_basis_is_application_derived(isolated,references,basis):
    _,_,evidence,candidate=inputs()
    candidate['comment_analysis'][0].update(evidence_ids=references,basis='raw_comments')
    parsed=providers.validate_report(candidate,evidence,require_creation=True)
    assert parsed['comment_analysis'][0]['basis']==basis


@pytest.mark.parametrize('unsupported',[
    dict(kind='title',text='评论主题统计'),
    dict(kind='brief',text='{"comment_analysis":{"total_comments":12,"themes":[]}}'),
    dict(kind='data_analysis',text='{"summary":{"content_count":12}}'),
    dict(kind='data_analysis',text='{"comment_analysis":{"total_comments":"12","themes":[]}}'),
])
def test_unrelated_evidence_cannot_support_comment_claims(isolated,unsupported):
    _,_,evidence,candidate=inputs()
    evidence=[e for e in evidence if e['id']!='A0001']+[dict(id='A0001',**unsupported)]
    with pytest.raises(providers.AnalysisError) as failure:
        providers.validate_report(candidate,evidence,require_creation=True)
    assert failure.value.diagnostics['issues'][0]['code']=='unsupported_evidence_type'
    # A rejected analysis does not hide structurally valid, source-backed drafts.
    assert artifacts.manifest(candidate,evidence)['creation']['ready_count']==1


def test_imported_statistics_keep_capabilities_without_trusting_model_labels(isolated):
    _,_,evidence,candidate=inputs()
    source,imported=prepare(dict(kind='report',reference_bundle=dict(report=candidate,evidence=evidence,source_kind='analytics')))
    upstream=next(e for e in imported if e['upstream_id']=='A0001')
    candidate['comment_analysis'][0]['evidence_ids']=[upstream['id']]
    candidate['summary_evidence_ids']=candidate['rewrites'][0]['evidence_ids']=[upstream['id']]
    candidate['sections']=[];candidate['ideas']=[]
    execution=next(e for e in evidence if e['kind']=='creation_execution')
    parsed=providers.validate_report(candidate,[*imported,execution],require_creation=True)
    assert parsed['comment_analysis'][0]['basis']=='comment_statistics'
    candidate['comment_analysis'][0]['evidence_ids']=['made-up']
    with pytest.raises(providers.AnalysisError,match='不存在的引用'):
        providers.validate_report(candidate,[*imported,execution],require_creation=True)


@pytest.mark.parametrize('imported',[False,True])
def test_local_comment_topic_counts_are_valid_statistics(isolated,imported):
    _,_,evidence,candidate=inputs()
    entry=dict(id='T0001',kind='comment_topics',text=json.dumps(dict(sample_size=1,groups=[])))
    if imported:entry.update(kind='source_excerpt',origin_kind='comment_topics',factual_source=False)
    candidate['comment_analysis'][0]['evidence_ids']=['T0001']
    parsed=providers.validate_report(candidate,[*evidence,entry],require_creation=True)
    assert parsed['comment_analysis'][0]['basis']=='comment_statistics'


def save_rejected():
    payload,source,evidence,candidate=inputs()
    job=store.submit(payload,'fixture',source)
    store.update(job['id'],status='failed',stage='analyze',evidence=evidence,error='旧规则拒绝统计引用')
    path=store.root()/'jobs'/job['id']/'rejected-report.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(dict(candidate=candidate,evidence_sha256=revalidation.evidence_hash(evidence),
        model='fixture-original-model',prompt_version='fixture-original-prompt',usage={'total_tokens':123},created_at='2026-01-01T00:00:00Z')))
    path.chmod(0o600)
    return job,path


def test_restore_and_preview_reuse_exact_response_and_never_generate(client,monkeypatch):
    job,path=save_rejected()
    def forbidden(*args,**kwargs):pytest.fail('Recovery must not call a provider')
    monkeypatch.setattr(providers,'analyze',forbidden)
    monkeypatch.setattr(providers.httpx,'AsyncClient',forbidden)
    base=f"/api/remix/jobs/{job['id']}"
    detail=client.get(base).json()
    assert detail['stored_response_available'] and detail['artifacts']['creation']['ready_count']==1
    preview=client.get(base+'/draft-candidates').json()
    assert preview['review_required'] and len(preview['items'])==1
    restored=client.post(base+'/revalidate').json()
    assert restored['status']=='completed' and restored['recovered_response']
    assert restored['report']['comment_analysis'][0]['basis']=='comment_statistics'
    assert restored['prompt_version']=='fixture-original-prompt'
    assert restored['model_generated_at']=='2026-01-01T00:00:00Z'
    assert restored['usage']=={'total_tokens':123}
    assert restored['artifacts']['validation']==dict(state='ready',human_review='required')
    path.unlink()
    assert client.post(base+'/revalidate').json()==restored
    exported=client.get(base+'/report.md').text
    assert '依据：本机评论统计' in exported and '未再次调用模型' in exported


def test_recovery_rejects_changed_sources_and_active_execution(client):
    job,path=save_rejected();base=f"/api/remix/jobs/{job['id']}"
    old=store.get(job['id'])
    store.update(job['id'],evidence=[*old['evidence'],dict(id='changed',kind='body',text='新来源')])
    assert client.post(base+'/revalidate').status_code==409
    assert client.get(base+'/draft-candidates').status_code==409
    store.update(job['id'],evidence=old['evidence'],status='running')
    assert client.post(base+'/revalidate').status_code==409
    assert store.get(job['id'])['status']=='running'


def test_topic_table_reads_archived_comments_without_rewriting_original_evidence(client):
    job,_=save_rejected();old=store.get(job['id'])
    store.update(job['id'],comment_topics=dict(rule_version='comment-topics-v1',sample_size=0,groups=[]))
    table=client.get(f"/api/remix/jobs/{job['id']}/comment-topics").json()
    assert table['sample_size']==1 and table['historical_recalculation']
    assert table['rule_version']==topics.RULE_VERSION
    assert table['groups'][0]['evidence_ids']==['C9999']
    assert store.get(job['id'])['evidence']==old['evidence']
    assert store.get(job['id'])['comment_topics']['sample_size']==0


def test_restore_checks_status_again_before_committing(isolated,monkeypatch):
    job,_=save_rejected();validate=providers.validate_report
    def changed(*args,**kwargs):
        parsed=validate(*args,**kwargs)
        store.update(job['id'],status='running')
        return parsed
    monkeypatch.setattr(providers,'validate_report',changed)
    with pytest.raises(ValueError,match='状态或来源已变化'):revalidation.restore(job['id'])
    assert store.get(job['id'])['status']=='running'


@pytest.mark.parametrize('bad',[None,{},dict(rewrites=None),dict(rewrites='invalid'),dict(rewrites=[None])])
def test_malformed_model_output_cannot_crash_artifact_diagnostics(isolated,bad):
    _,_,evidence,_=inputs()
    state=artifacts.manifest(bad,evidence)
    assert state['creation']['ready_count']==0
    assert state['validation']['state']=='review_required'
