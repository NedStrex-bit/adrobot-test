import asyncio
import httpx
import pytest
from fastapi import HTTPException
from app import keitaro
from app.config import settings

@pytest.mark.parametrize('status',[401,402,404,500])
def test_http_errors_hide_upstream_secrets(monkeypatch,status):
    real_client=httpx.AsyncClient
    transport=httpx.MockTransport(lambda request:httpx.Response(status,json={'error':'private-token'}))
    monkeypatch.setattr(settings,'demo_mode',False)
    monkeypatch.setattr(settings,'keitaro_api_key','secret')
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real_client(transport=transport,**kwargs))
    with pytest.raises(HTTPException) as error:asyncio.run(keitaro.request('GET','offers'))
    assert error.value.status_code==502
    assert 'private-token' not in error.value.detail

def test_real_adapter_url_header_payload(monkeypatch):
    real_client=httpx.AsyncClient
    def handle(request):
        assert str(request.url)=='https://tracker.example.com/admin_api/v1/streams/42'
        assert request.headers['Api-Key']=='test-key'
        assert request.method=='PUT'
        assert request.content==b'{"offers":[]}'
        return httpx.Response(200,json={'id':42})
    monkeypatch.setattr(settings,'demo_mode',False)
    monkeypatch.setattr(settings,'keitaro_url','https://tracker.example.com/')
    monkeypatch.setattr(settings,'keitaro_api_key','test-key')
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handle),**kwargs))
    assert asyncio.run(keitaro.request('PUT','streams/42',{'offers':[]}))=={'id':42}

def test_network_error(monkeypatch):
    real_client=httpx.AsyncClient
    def fail(request): raise httpx.ConnectError('unreachable')
    monkeypatch.setattr(settings,'demo_mode',False)
    monkeypatch.setattr(settings,'keitaro_api_key','test-key')
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(fail),**kwargs))
    with pytest.raises(HTTPException) as error:asyncio.run(keitaro.request('GET','offers'))
    assert error.value.status_code==502

def test_partial_creation_kept_disabled(client,monkeypatch):
    client.put('/api/settings',json={'domain_id':1,'group_id':1,'traffic_source_id':1})
    original=keitaro.request
    async def fail(method,path,payload=None):
        if method=='POST' and path=='streams' and payload['position']==2:raise HTTPException(502,'failed')
        return await original(method,path,payload)
    monkeypatch.setattr(keitaro,'request',fail)
    result=client.post('/api/campaigns',json={'name':'Partial','geo':['AU'],'offer_id':3749})
    assert result.status_code==502
    campaigns=client.get('/api/campaigns').json()
    assert len(campaigns)==1
    assert campaigns[0]['state']=='disabled'

@pytest.mark.parametrize('method,expected_calls',[('GET',2),('POST',1)])
def test_read_retries_but_writes_do_not(monkeypatch,method,expected_calls):
    real_client=httpx.AsyncClient
    calls=[]
    def handle(request):
        calls.append(request.method)
        if len(calls)==1: raise httpx.ConnectError('temporary connection failure')
        return httpx.Response(200,json=[])
    monkeypatch.setattr(settings,'demo_mode',False)
    monkeypatch.setattr(settings,'keitaro_api_key','test-key')
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handle),**kwargs))
    if method=='GET': assert asyncio.run(keitaro.request(method,'offers'))==[]
    else:
        with pytest.raises(HTTPException):asyncio.run(keitaro.request(method,'campaigns',{'name':'test'}))
    assert len(calls)==expected_calls
