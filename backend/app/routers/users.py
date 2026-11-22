"""Kullanıcı yönetimi: açık kayıt yok, kullanıcıları sadece yönetici oluşturur."""
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..deps import require_admin
from ..models import Role, User
from ..schemas import UserCreate, UserCreated, UserOut, UserUpdate
from ..security import hash_password
from ..services.core import audit

router = APIRouter(prefix="/users", tags=["Kullanıcılar (yönetici)"])


@router.get("", response_model=list[UserOut])
def list_users(s: Session = Depends(get_session), _: User = Depends(require_admin)):
    return s.exec(select(User).order_by(User.username)).all()


@router.post("", response_model=UserCreated, status_code=201)
def create_user(body: UserCreate, s: Session = Depends(get_session), admin: User = Depends(require_admin)):
    if s.exec(select(User).where(User.username == body.username)).first():
        raise HTTPException(409, "Bu kullanıcı adı zaten var")
    temp = None if body.password else secrets.token_urlsafe(9)
    user = User(username=body.username, full_name=body.full_name, role=body.role,
                password_hash=hash_password(body.password or temp))
    s.add(user)
    audit(s, admin.id, "user.create", user.id, body.role.value)
    s.commit()
    s.refresh(user)
    return UserCreated(**user.model_dump(), temporary_password=temp)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserUpdate, s: Session = Depends(get_session),
                admin: User = Depends(require_admin)):
    user = s.get(User, user_id)
    if not user:
        raise HTTPException(404, "Kullanıcı bulunamadı")
    if user.id == admin.id and (body.active is False or (body.role and body.role != Role.admin)):
        raise HTTPException(400, "Kendi yönetici yetkinizi kaldıramazsınız")
    for k in ("full_name", "role", "active"):
        v = getattr(body, k)
        if v is not None:
            setattr(user, k, v)
    if body.password:
        user.password_hash = hash_password(body.password)
    s.add(user)
    audit(s, admin.id, "user.update", user.id, body.model_dump_json(exclude={"password"}, exclude_none=True))
    s.commit()
    s.refresh(user)
    return user
