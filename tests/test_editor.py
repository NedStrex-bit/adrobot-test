from copy import deepcopy
import pytest
from app.db import read, write
from app.shares import rebalance

def endpoint(campaign):
    cid, sid, _ = campaign
    return f'/api/campaigns/{cid}/streams/{sid}/draft'

def mutate(client, path, draft, action, **kwargs):
    r = client.post(path, json={'revision':draft['revision'],'action':action, **kwargs})
    assert r.status_code==200, r.text
    return r.json()

def test_creation_routing(client, campaign):
    cid, sid, streams = campaign
    assert len(streams)==2
    geo, offers = sorted(streams,key=lambda x:x['position'])
    assert geo['filters']==[{'name':'country','mode':'accept','payload':['AU','MX']}]
    assert geo['action_payload']=='https://google.com'
    assert geo['type']=='regular'
    assert offers['type']=='default'
    assert offers['offers']==[{'offer_id':3749,'share':100,'state':'active'}]
    created = client.get('/api/campaigns').json()[0]
    assert (created['domain_id'],created['group_id'],created['traffic_source_id'])==(1,1,1)

def test_add_pin_remove_restore_cancel_push(client,campaign):
    path=endpoint(campaign)
    d=client.get(path).json()
    assert client.post(path,json={'revision':d['revision'],'action':'remove','offer_id':3749}).status_code==422
    for oid in [3717,11111,11112]: d=mutate(client,path,d,'add',offer_id=oid)
    assert [o['share'] for o in d['offers']]==[25]*4
    # Draft survives reload; tracker is untouched until push.
    assert client.get(path).json()==d
    assert len(client.get(f'/api/campaigns/{campaign[0]}/streams').json()[1]['offers'])==1
    d=mutate(client,path,d,'share',offer_id=3749,share=25)
    d=mutate(client,path,d,'remove',offer_id=11111)
    assert [o['share'] for o in d['offers']]==[25,38,0,37]
    d=mutate(client,path,d,'restore',offer_id=11111)
    assert [o['share'] for o in d['offers']]==[25]*4
    d=mutate(client,path,d,'push')
    assert not d['dirty']
    assert len(d['offers'])==4
    d=mutate(client,path,d,'remove',offer_id=11111)
    d=mutate(client,path,d,'cancel')
    assert len(d['offers'])==4 and all(not o['removed'] for o in d['offers'])

def test_stale_revision(client,campaign):
    path=endpoint(campaign); d=client.get(path).json()
    mutate(client,path,d,'add',offer_id=3717)
    assert client.post(path,json={'revision':d['revision'],'action':'add','offer_id':11111}).status_code==409

def test_external_conflict_preserves_draft(client,campaign):
    path=endpoint(campaign); d=client.get(path).json()
    d=mutate(client,path,d,'add',offer_id=3717)
    remote=read('simulator');remote['streams'][1]['name']='Changed externally';write('simulator',remote)
    assert client.post(path,json={'revision':d['revision'],'action':'push'}).status_code==409
    assert client.get(path).json()==d

def test_preserves_landings_and_inactive_offers(client,campaign):
    state=read('simulator')
    state['streams'][1]['landings']=[{'landing_id':7,'share':100,'state':'active'}]
    state['streams'][1]['offers'].append({'offer_id':11112,'share':0,'state':'disabled'})
    write('simulator',state)
    path=endpoint(campaign);d=client.get(path).json();d=mutate(client,path,d,'add',offer_id=3717);mutate(client,path,d,'push')
    stream=read('simulator')['streams'][1]
    assert stream['landings']==[{'landing_id':7,'share':100,'state':'active'}]
    assert stream['offers'][-1]=={'offer_id':11112,'share':0,'state':'disabled'}

def test_invalid_input(client,campaign):
    for geo in [['XX'],['Australia'],[],['']]:
        assert client.post('/api/campaigns',json={'name':'a','geo':geo,'offer_id':3749}).status_code==422
    path=endpoint(campaign);d=client.get(path).json()
    assert client.post(path,json={'revision':d['revision'],'action':'share','offer_id':3749,'share':101}).status_code==422
    assert client.post(path,json={'revision':d['revision'],'action':'add','offer_id':3749}).status_code==422
    assert client.post(path,json={'revision':d['revision'],'action':'add','offer_id':99999}).status_code==404

def test_all_pinned_validation():
    with pytest.raises(ValueError):rebalance([{'share':50,'pinned':True}])
    with pytest.raises(ValueError):rebalance([{'share':70,'pinned':True},{'share':40,'pinned':True}])
    with pytest.raises(ValueError):rebalance([])
    assert rebalance([{'share':100,'pinned':True}])[0]['share']==100

@pytest.mark.parametrize('count',range(1,101))
def test_rounding_always_totals_100(count):
    result=rebalance([{'share':0,'pinned':False} for _ in range(count)])
    assert sum(o['share'] for o in result)==100
    assert max(o['share'] for o in result)-min(o['share'] for o in result)<=1
