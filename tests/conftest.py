import os
import tempfile
from pathlib import Path

_test_dir = tempfile.TemporaryDirectory(prefix='adrobot-test-')
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(_test_dir.name) / 'tests.db')
os.environ['DEMO_MODE'] = 'true'

import pytest
from sqlalchemy import delete
from fastapi.testclient import TestClient
from app.main import app
from app.db import Base, engine, Record, Session

@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    with Session.begin() as session: session.execute(delete(Record))
    yield

@pytest.fixture
def client():
    with TestClient(app) as client: yield client

@pytest.fixture
def campaign(client):
    assert client.put('/api/settings', json={'domain_id':1,'group_id':1,'traffic_source_id':1}).status_code == 200
    response = client.post('/api/campaigns', json={'name':'Test campaign','geo':['au','MX','AU'],'offer_id':3749})
    assert response.status_code == 201, response.text
    cid = response.json()['id']
    streams = client.get(f'/api/campaigns/{cid}/streams').json()
    sid = next(s['id'] for s in streams if s['schema']=='landings')
    return cid, sid, streams
