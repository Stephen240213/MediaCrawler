"""Project references, immutable production associations and recovery CAS use fixtures only."""
import json
from datetime import datetime,timezone
import pytest
from api.services import workbench_store
from api.services.remix import projects,store,production,publications,workflow
from api.services.remix.schemas import ProjectInput,ProjectLink,EditorSnapshot,JobInput,PublicationInput,ObservationInput
from tests.test_content_remix import isolated,client
from tests.test_remix_learning import finished
from tests.test_remix_production import payload,approved


def test_project_versions_links_and_automatic_descendants(isolated):
    p=projects.save(ProjectInput(title='旅居内容项目',brief='费用与条件'),'fixture')
    job,version=finished(None)
    projects.link(p['id'],ProjectLink(kind='job',resource_id=job['id']))
    projects.link(p['id'],ProjectLink(kind='job',resource_id=job['id']))
    detail=projects.detail(p['id'])
    assert len(detail['links'])==1 and detail['versions'][0]['id']==version['id']
    item=production.save(job['id'],payload(job,version,checks=approved()),'fixture')
    pub=publications.save(job['id'],PublicationInput(version_id=version['id'],production_id=item['id'],url='https://www.xiaohongshu.com/explore/project-published',published_at=datetime.now(timezone.utc).isoformat()),'fixture')
    assert projects.detail(p['id'])['productions'][0]['id']==item['id']
    assert projects.detail(p['id'])['publications'][0]['production_id']==item['id']
    assert projects.detail(p['id'])['stage']=='已发布'
    changed=projects.save(ProjectInput(title='改名',version=1),'fixture',p['id'])
    assert changed['version']==2
    with pytest.raises(ValueError,match='其他页面'):projects.save(ProjectInput(title='旧稿',version=1),'fixture',p['id'])
    projects.unlink(p['id'],ProjectLink(kind='job',resource_id=job['id']))
    assert not projects.detail(p['id'])['links'] and store.get(job['id'])


def test_new_jobs_link_atomically_and_archived_projects_reject(isolated):
    p=projects.save(ProjectInput(title='项目'),'fixture')
    input=JobInput(source='xhs',content_id='fixture-note',project_id=p['id'],request_key='project-job-key-1234')
    source=workflow.source_for(input);job=store.submit(input,'fixture',source)
    assert projects.detail(p['id'])['links'][0]['resource_id']==job['id']
    assert store.submit(input,'fixture',source)['id']==job['id']
    projects.save(ProjectInput(title='项目',archived=True,version=1),'fixture',p['id'])
    bad=input.model_copy(update={'request_key':'archived-project-1234'})
    before=store.listing()['total']
    with pytest.raises(ValueError,match='归档'):store.submit(bad,'fixture',source)
    assert store.listing()['total']==before
    assert not projects.listing() and len(projects.listing(True))==1


def test_recovery_is_actor_scoped_bounded_and_optimistic(isolated):
    p=projects.save(ProjectInput(title='项目'),'fixture')
    value=projects.save_snapshot('A','project',p['id'],EditorSnapshot(revision=0,data={'notes':'尚未保存的编辑'}))
    assert value['revision']==1
    assert projects.snapshot('B','project',p['id'])['data'] is None
    assert projects.snapshot('A','project',p['id'])['data']['notes']=='尚未保存的编辑'
    with pytest.raises(ValueError,match='其他页面'):projects.save_snapshot('A','project',p['id'],EditorSnapshot(revision=0,data={'notes':'过期'}))
    with pytest.raises(ValueError,match='300KB'):projects.save_snapshot('A','project',p['id'],EditorSnapshot(revision=1,data={'notes':'长'*110000}))
    assert projects.get(p['id'])['notes']==''


def test_publication_cannot_bind_another_version_or_job(isolated):
    job,version=finished(None);other,other_version=finished(None)
    item=production.save(other['id'],payload(other,other_version),'fixture')
    with pytest.raises(ValueError,match='制作版本'):
        publications.save(job['id'],PublicationInput(version_id=version['id'],production_id=item['id'],url='https://www.xiaohongshu.com/explore/different',published_at=datetime.now(timezone.utc).isoformat()),'fixture')


def test_project_api_and_recovery_survive_reread(client):
    response=client.post('/api/remix/projects',json={'title':'API项目'})
    assert response.status_code==200,response.text
    p=response.json()
    assert client.post(f"/api/remix/projects/{p['id']}/links",json={'kind':'job','resource_id':'f'*32}).status_code==409
    result=client.put(f"/api/remix/editors/project/{p['id']}",json={'revision':0,'data':{'notes':'恢复内容'}})
    assert result.status_code==200
    assert client.get(f"/api/remix/editors/project/{p['id']}").json()['data']['notes']=='恢复内容'
    assert client.put(f"/api/remix/editors/project/{p['id']}",json={'revision':0,'data':{}}).status_code==409
    assert client.get('/api/remix/editors/unknown/current').status_code==404
