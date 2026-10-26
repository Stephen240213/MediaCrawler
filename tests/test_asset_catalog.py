import io
from pathlib import Path
from PIL import Image
import pytest
from api.services import asset_catalog
from api.routers import workbench
from tests.test_content_remix import isolated,client


def image(path,color='red'):
    path.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(800,600),color).save(path)


def test_index_avoids_repeat_scans_and_hashes_and_can_refresh(isolated,monkeypatch):
    root=workbench.DATA_ROOT;image(root/'a.png')
    first=asset_catalog.listing(root,page=1,page_size=1)
    assert first['total']==1 and first['assets'][0]['sha256']
    thumb=asset_catalog.thumbnail(root,'a.png');assert Image.open(thumb).size==(480,360)
    original=Path.rglob
    monkeypatch.setattr(Path,'rglob',lambda *_:(_ for _ in ()).throw(AssertionError('unexpected rescan')))
    assert asset_catalog.listing(root)['assets'][0]['id']=='a.png'
    monkeypatch.setattr(Path,'rglob',original)
    image(root/'b.png','blue');assert asset_catalog.listing(root)['total']==1
    asset_catalog.refresh(root);assert asset_catalog.listing(root)['total']==2
    (root/'a.png').unlink();asset_catalog.refresh(root);assert asset_catalog.listing(root)['total']==1


def test_index_query_paging_archive_and_thumbnail_api(client):
    root=workbench.DATA_ROOT;image(root/'first.png');image(root/'second.png')
    response=client.get('/api/workbench/assets',params={'page':1,'page_size':1})
    assert response.status_code==200 and response.json()['total']==2 and len(response.json()['assets'])==1
    assert client.get('/api/workbench/assets',params={'search':'second','page':1}).json()['total']==1
    assert client.get('/api/workbench/assets/thumb/first.png').headers['content-type']=='image/jpeg'
    client.patch('/api/workbench/assets/state/first.png',json={'archived':True})
    assert client.get('/api/workbench/assets',params={'archived':True}).json()['total']==1
    assert client.post('/api/workbench/assets/reindex').status_code==200


def test_symlinks_do_not_enter_index_or_thumbnails(isolated):
    root=workbench.DATA_ROOT;image(root/'real.png');outside=isolated/'outside.png';image(outside)
    (root/'alias.png').symlink_to(outside)
    assert asset_catalog.listing(root)['total']==1
    with pytest.raises(ValueError):asset_catalog.thumbnail(root,'alias.png')
