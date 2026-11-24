import asyncio
import copy
import hashlib
import json
import secrets
from pathlib import Path
from typing import Literal
import pycountry
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from .config import settings
from .db import read, write
from . import keitaro
from .shares import rebalance

app = FastAPI(title='Adrobot', version='1.0.0')
static = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=static), name='static')
# One worker: serializes mutations and protects revisions and simulator writes.
mutation_lock = asyncio.Lock()

class Defaults(BaseModel):
    domain_id: int = Field(gt=0)
    group_id: int = Field(gt=0)
    traffic_source_id: int = Field(gt=0)

class CreateCampaign(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    geo: list[str] = Field(min_length=1, max_length=249)
    offer_id: int = Field(gt=0)

    @field_validator('name')
    @classmethod
    def name_valid(cls, value):
        if not value.strip(): raise ValueError('Название не может быть пустым')
        return value.strip()

    @field_validator('geo')
    @classmethod
    def geo_valid(cls, values):
        result = list(dict.fromkeys(x.strip().upper() for x in values))
        if any(pycountry.countries.get(alpha_2=x) is None for x in result):
            raise ValueError('Используйте двухбуквенные ISO-коды стран, например AU, MX, RO')
        return result

class Edit(BaseModel):
    revision: int = Field(ge=1)
    action: Literal['add', 'remove', 'restore', 'pin', 'share', 'cancel', 'push']
    offer_id: int = Field(default=0, ge=0)
    share: int | None = Field(default=None, ge=0, le=100)

class Revision(BaseModel):
    revision: int = Field(ge=1)

def fingerprint(stream):
    # Include the full source: never overwrite an externally changed stream.
    return hashlib.sha256(json.dumps(stream, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

async def catalogs():
    domains, groups, sources = await asyncio.gather(keitaro.request('GET', 'domains'), keitaro.request('GET', 'groups?type=campaigns'), keitaro.request('GET', 'traffic_sources'))
    return {'domains': domains, 'groups': groups, 'sources': sources}

async def validate_defaults(data):
    options = await catalogs()
    for key, collection in [('domain_id', 'domains'), ('group_id', 'groups'), ('traffic_source_id', 'sources')]:
        if not any(int(x['id']) == data[key] for x in options[collection]):
            raise HTTPException(422, f'Выбранный {key} отсутствует в Keitaro')

@app.get('/')
async def index(): return FileResponse(static / 'index.html')

@app.get('/api/settings')
async def get_settings():
    return {'demo': settings.demo_mode, 'defaults': read('defaults'), **await catalogs()}

@app.put('/api/settings')
async def save_settings(data: Defaults):
    async with mutation_lock:
        await validate_defaults(data.model_dump())
        write('defaults', data.model_dump())
    return data

@app.get('/api/offers')
async def offers(q: str = Query('', max_length=200)):
    items = await keitaro.request('GET', 'offers')
    return [x for x in items if x.get('state') not in ('deleted', 'disabled') and q.casefold() in f"{x['id']} {x['name']}".casefold()][:50]

@app.get('/api/campaigns')
async def campaigns():
    return [x for x in await keitaro.request('GET', 'campaigns') if x.get('state') != 'deleted']

@app.post('/api/campaigns', status_code=201)
async def create_campaign(data: CreateCampaign):
    async with mutation_lock:
        defaults = read('defaults')
        if not defaults: raise HTTPException(422, 'Сначала сохраните домен, группу и источник в настройках')
        await validate_defaults(defaults)
        if not any(x['id'] == data.offer_id and x.get('state') not in ('deleted', 'disabled') for x in await keitaro.request('GET', 'offers')):
            raise HTTPException(422, 'Оффер не найден или недоступен')
        campaign = await keitaro.request('POST', 'campaigns', {'name': data.name, 'alias': secrets.token_hex(5), 'type': 'position', 'state': 'disabled', **defaults})
        cid = campaign['id']
        try:
            await keitaro.request('POST', 'streams', {'campaign_id': cid, 'name': 'Geo → Google', 'type': 'regular', 'position': 1, 'state': 'active', 'collect_clicks': True, 'schema': 'redirect', 'action_type': 'http', 'action_payload': 'https://google.com', 'filter_or': False, 'filters': [{'name': 'country', 'mode': 'accept', 'payload': data.geo}]})
            await keitaro.request('POST', 'streams', {'campaign_id': cid, 'name': 'Offers', 'type': 'regular', 'position': 2, 'state': 'active', 'collect_clicks': True, 'action_type': 'http', 'schema': 'landings', 'landings': [], 'offers': [{'offer_id': data.offer_id, 'share': 100, 'state': 'active'}]})
            campaign = await keitaro.request('PUT', f'campaigns/{cid}', {'state': 'active'})
        except HTTPException:
            # Archive partial campaign. Ambiguous network failures still require inspection.
            try:
                await keitaro.request('DELETE', f'campaigns/{cid}')
                message = f'Не удалось создать потоки. Кампания #{cid} отправлена в архив; проверьте её перед повтором.'
            except HTTPException:
                message = f'Не удалось создать потоки и архивировать кампанию #{cid}. Проверьте её в Keitaro.'
            raise HTTPException(502, message)
        return campaign

@app.get('/api/campaigns/{campaign_id}/streams')
async def streams(campaign_id: int):
    return await keitaro.request('GET', f'campaigns/{campaign_id}/streams')

async def source_stream(campaign_id, stream_id):
    streams = await keitaro.request('GET', f'campaigns/{campaign_id}/streams')
    stream = next((s for s in streams if s['id'] == stream_id), None)
    if not stream: raise HTTPException(404, 'Поток не найден в кампании')
    if stream.get('schema') != 'landings': raise HTTPException(422, 'Этот поток не содержит офферы')
    return stream

async def build_draft(campaign_id, stream_id, old=None):
    stream = await source_stream(campaign_id, stream_id)
    names = {o['id']: o['name'] for o in await keitaro.request('GET', 'offers')}
    active = [o for o in stream.get('offers', []) if o.get('state', 'active') == 'active']
    draft = {'campaign_id': campaign_id, 'stream_id': stream_id, 'source': stream, 'fingerprint': fingerprint(stream), 'revision': (old or {}).get('revision', 0) + 1, 'dirty': False, 'offers': [{'offer_id': o['offer_id'], 'name': names.get(o['offer_id'], f"Offer #{o['offer_id']}"), 'share': o['share'], 'pinned': False, 'removed': False} for o in active]}
    write(f'draft:{campaign_id}:{stream_id}', draft)
    return draft

@app.get('/api/campaigns/{campaign_id}/streams/{stream_id}/draft')
async def draft(campaign_id: int, stream_id: int):
    async with mutation_lock:
        old = read(f'draft:{campaign_id}:{stream_id}')
        return old or await build_draft(campaign_id, stream_id)

@app.post('/api/campaigns/{campaign_id}/streams/{stream_id}/refresh')
async def refresh(campaign_id: int, stream_id: int, data: Revision):
    async with mutation_lock:
        old = read(f'draft:{campaign_id}:{stream_id}')
        if not old or old['revision'] != data.revision: raise HTTPException(409, 'Черновик изменён в другой вкладке. Откройте поток заново.')
        return await build_draft(campaign_id, stream_id, old)

@app.post('/api/campaigns/{campaign_id}/streams/{stream_id}/draft')
async def edit(campaign_id: int, stream_id: int, data: Edit):
    async with mutation_lock:
        key = f'draft:{campaign_id}:{stream_id}'
        current = read(key)
        if not current: raise HTTPException(404, 'Сначала загрузите поток')
        if current['revision'] != data.revision: raise HTTPException(409, 'Черновик изменён в другой вкладке. Откройте поток заново.')
        result = copy.deepcopy(current)
        if data.action == 'cancel':
            # Cancel restores original snapshot, without silently accepting remote changes.
            result['offers'] = [{'offer_id': o['offer_id'], 'name': next((x['name'] for x in current['offers'] if x['offer_id'] == o['offer_id']), str(o['offer_id'])), 'share': o['share'], 'pinned': False, 'removed': False} for o in current['source'].get('offers', []) if o.get('state', 'active') == 'active']
            result['dirty'] = False
        elif data.action == 'push':
            remote = await source_stream(campaign_id, stream_id)
            if fingerprint(remote) != current['fingerprint']:
                raise HTTPException(409, 'Поток изменён в Keitaro. Загрузите свежую версию; черновик сохранён.')
            active = [o for o in current['offers'] if not o['removed']]
            if not active or sum(o['share'] for o in active) != 100:
                raise HTTPException(422, 'Нужен хотя бы один оффер и сумма долей 100%')
            # Preserve inactive offers and landings; update only the offer collection.
            inactive = [{'offer_id': o['offer_id'], 'share': o['share'], 'state': o.get('state', 'disabled')} for o in remote.get('offers', []) if o.get('state', 'active') != 'active' and o['offer_id'] not in {x['offer_id'] for x in active}]
            await keitaro.request('PUT', f'streams/{stream_id}', {'offers': [{'offer_id': o['offer_id'], 'share': o['share'], 'state': 'active'} for o in active] + inactive})
            return await build_draft(campaign_id, stream_id, current)
        else:
            offer = next((o for o in result['offers'] if o['offer_id'] == data.offer_id), None)
            if data.action == 'add':
                if offer: raise HTTPException(422, 'Оффер уже в списке. Для удалённого используйте восстановление.')
                found = next((o for o in await keitaro.request('GET', 'offers') if o['id'] == data.offer_id and o.get('state') not in ('deleted', 'disabled')), None)
                if not found: raise HTTPException(404, 'Оффер не найден')
                result['offers'].append({'offer_id': data.offer_id, 'name': found['name'], 'share': 0, 'pinned': False, 'removed': False})
            else:
                if not offer: raise HTTPException(404, 'Оффер отсутствует в черновике')
                if data.action == 'remove': offer.update(removed=True, pinned=False)
                elif data.action == 'restore': offer.update(removed=False, pinned=False)
                elif offer['removed']: raise HTTPException(422, 'Сначала восстановите оффер')
                elif data.action == 'pin': offer['pinned'] = not offer['pinned']
                elif data.action == 'share':
                    if data.share is None: raise HTTPException(422, 'Укажите долю')
                    offer.update(share=data.share, pinned=True)
            try: rebalance(result['offers'])
            except ValueError as exc: raise HTTPException(422, str(exc))
            result['dirty'] = True
        result['revision'] += 1
        write(key, result)
        return result
