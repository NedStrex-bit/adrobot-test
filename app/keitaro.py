import asyncio
import copy
import httpx
from fastapi import HTTPException
from .config import settings
from .db import read, write

DEMO_OFFERS = [
    {'id': 3749, 'name': 'Miaflow · Romania · Spin'},
    {'id': 3717, 'name': 'Miaflow · Romania · Prize'},
    {'id': 11111, 'name': 'FitoMishi · Colombia'},
    {'id': 11112, 'name': 'Onys · Chile'},
    {'id': 11205, 'name': 'Alvidemia · Mexico'},
]

async def request(method, path, payload=None):
    if settings.demo_mode:
        return demo(method, path, payload)
    if not settings.keitaro_api_key:
        raise HTTPException(503, 'Укажите KEITARO_API_KEY в .env')
    attempts = 3 if method == 'GET' else 1
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                response = await client.request(method, settings.keitaro_url.rstrip('/') + '/admin_api/v1/' + path, headers={'Api-Key': settings.keitaro_api_key}, json=payload)
            if method == 'GET' and response.status_code in (502, 503, 504) and attempt < attempts - 1:
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            if response.is_error:
                raise HTTPException(502, f'Keitaro вернул HTTP {response.status_code}. Обновите данные перед повтором.')
            return response.json() if response.content else None
        except httpx.RequestError:
            if attempt < attempts - 1:
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            message = ('Не удалось загрузить данные Keitaro после трёх попыток. Попробуйте обновить список.'
                       if method == 'GET' else
                       'Соединение с Keitaro прервалось. Результат операции может быть неизвестен; обновите данные перед повтором.')
            raise HTTPException(502, message)

def demo(method, path, payload):
    state = read('simulator') or {'campaigns': [], 'streams': [], 'next_id': 100}
    parts = path.split('/')
    entity = parts[0]
    if method == 'GET':
        if path == 'offers': return copy.deepcopy(DEMO_OFFERS)
        if path == 'domains': return [{'id': 1, 'name': 'demo.tracker.local'}]
        if path == 'groups?type=campaigns': return [{'id': 1, 'name': 'Test campaigns'}]
        if path == 'traffic_sources': return [{'id': 1, 'name': 'Facebook'}]
        if entity == 'campaigns' and len(parts) == 3:
            return [s for s in state['streams'] if s['campaign_id'] == int(parts[1])]
        if len(parts) == 1: return copy.deepcopy(state[entity])
        return next((copy.deepcopy(x) for x in state[entity] if x['id'] == int(parts[1])), None)
    if method == 'POST':
        state['next_id'] += 1
        obj = {**payload, 'id': state['next_id']}
        state[entity].append(obj)
    elif method == 'PUT':
        obj = next(x for x in state[entity] if x['id'] == int(parts[1]))
        obj.update(payload)
    elif method == 'DELETE':
        state[entity] = [x for x in state[entity] if x['id'] != int(parts[1])]
        state['streams'] = [x for x in state['streams'] if x.get('campaign_id') != int(parts[1])]
        obj = None
    write('simulator', state)
    return copy.deepcopy(obj)
