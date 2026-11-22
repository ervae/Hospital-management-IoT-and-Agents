from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..deps import current_user
from ..models import User
from ..schemas import LoginIn, PasswordChange, TokenOut, UserOut
from ..security import create_token, hash_password, verify_password
from ..services.core import audit

router = APIRouter(prefix="/auth", tags=["Giriş"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, s: Session = Depends(get_session)):
    user = s.exec(select(User).where(User.username == body.username)).first()
    if not user or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Kullanıcı adı veya parola hatalı")
    audit(s, user.id, "login")
    s.commit()
    return TokenOut(access_token=create_token(user.id, user.role.value), user=UserOut(**user.model_dump()))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.post("/change-password", status_code=204)
def change_password(body: PasswordChange, user: User = Depends(current_user),
                    s: Session = Depends(get_session)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "Mevcut parola hatalı")
    user.password_hash = hash_password(body.new_password)
    s.add(user)
    audit(s, user.id, "password.change", user.id)
    s.commit()
