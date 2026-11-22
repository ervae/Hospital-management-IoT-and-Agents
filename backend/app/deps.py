"""Kimlik doğrulama bağımlılıkları: kullanıcı (JWT), cihaz (X-Device-Key), agent (X-Agent-Key)."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from .db import get_session
from .models import Agent, Device, Role, User, utcnow
from .security import decode_token, hash_key

bearer = HTTPBearer(auto_error=False)


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(bearer),
                 s: Session = Depends(get_session)) -> User:
    if not cred:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Giriş gerekli")
    payload = decode_token(cred.credentials)
    user = s.get(User, payload["sub"]) if payload else None
    if not user or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Oturum geçersiz")
    return user


def require_role(*roles: Role):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu işlem için yetkiniz yok")
        return user
    return dep


require_admin = require_role(Role.admin)
require_operator = require_role(Role.admin, Role.operator)


def current_device(x_device_key: str = Header(...), s: Session = Depends(get_session)) -> Device:
    dev = s.exec(select(Device).where(Device.key_hash == hash_key(x_device_key))).first()
    if not dev or not dev.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Geçersiz cihaz anahtarı")
    dev.last_seen = utcnow()
    s.add(dev)
    return dev


def current_agent(x_agent_key: str = Header(...), s: Session = Depends(get_session)) -> Agent:
    ag = s.exec(select(Agent).where(Agent.key_hash == hash_key(x_agent_key))).first()
    if not ag or not ag.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Geçersiz agent anahtarı")
    ag.last_seen = utcnow()
    s.add(ag)
    return ag
