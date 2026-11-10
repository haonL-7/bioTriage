# -*- coding: utf-8 -*-
"""自带密钥（BYOK）端点：/api/me/api-key 的查看、设置、删除。

安全约定：明文 key 只在请求处理期间存在于内存；响应里永远只回末四位提示。
"""
import re

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .db import get_db
from .models import utcnow
from .router import account_db_guard, resolve_user, _unauthorized
from .secrets_store import available, encrypt_secret, key_hint, unavailable_reason

router = APIRouter()

KEY_RE = re.compile(r'^sk-[A-Za-z0-9_\-]{16,200}$')


class ApiKeyIn(BaseModel):
    key: str


def _status(user) -> dict:
    return {
        'configured': bool(getattr(user, 'api_key_enc', None)),
        'hint': getattr(user, 'api_key_hint', None) or '',
        'updated_at': user.api_key_updated_at.isoformat() if getattr(user, 'api_key_updated_at', None) else None,
        'encryption_available': available(),
    }


@router.get('/me/api-key')
@account_db_guard(401, {'error': 'account database unavailable'})
async def get_api_key(request: Request, db: Session = Depends(get_db)):
    user, invalid_token = resolve_user(request, db)
    if user is None:
        return _unauthorized(invalid_token)
    payload = _status(user)
    if not payload['encryption_available']:
        payload['warning'] = unavailable_reason()
    return payload


@router.put('/me/api-key')
@account_db_guard(401, {'error': 'account database unavailable'})
async def set_api_key(body: ApiKeyIn, request: Request, db: Session = Depends(get_db)):
    user, invalid_token = resolve_user(request, db)
    if user is None:
        return _unauthorized(invalid_token)
    if not available():
        return {'error': 'key storage unavailable: %s' % unavailable_reason(), 'configured': False}

    plain = (body.key or '').strip()
    if not KEY_RE.match(plain):
        return {'error': 'looks like an invalid DeepSeek key (expected sk-... with 16+ chars)', 'configured': False}

    user.api_key_enc = encrypt_secret(plain)
    user.api_key_hint = key_hint(plain)
    user.api_key_updated_at = utcnow()
    db.commit()
    return _status(user)


@router.delete('/me/api-key')
@account_db_guard(401, {'error': 'account database unavailable'})
async def delete_api_key(request: Request, db: Session = Depends(get_db)):
    user, invalid_token = resolve_user(request, db)
    if user is None:
        return _unauthorized(invalid_token)
    user.api_key_enc = None
    user.api_key_hint = None
    user.api_key_updated_at = None
    db.commit()
    return _status(user)
