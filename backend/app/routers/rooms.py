from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, col, select

from ..db import get_session
from ..deps import current_user, require_admin, require_operator
from ..models import DEFAULT_PRIORITY, Command, Reading, Room, User, utcnow
from ..schemas import (CommandOut, ManualCommandIn, RoomCreate, RoomOut, RoomState, SeriesPoint)
from ..services import core

router = APIRouter(prefix="/rooms", tags=["Odalar"])


@router.get("", response_model=list[RoomState])
def list_rooms(s: Session = Depends(get_session), _: User = Depends(current_user)):
    return core.room_states(s)


@router.post("", response_model=RoomOut, status_code=201)
def create_room(body: RoomCreate, s: Session = Depends(get_session), admin: User = Depends(require_admin)):
    if s.exec(select(Room).where(Room.code == body.code)).first():
        raise HTTPException(409, "Bu oda kodu zaten var")
    data = body.model_dump()
    if data["priority"] is None:
        data["priority"] = DEFAULT_PRIORITY[body.type]
    room = Room(**data)
    s.add(room)
    core.audit(s, admin.id, "room.create", room.id, body.code)
    s.commit()
    s.refresh(room)
    return room


@router.get("/{room_id}", response_model=RoomState)
def get_room(room_id: str, s: Session = Depends(get_session), _: User = Depends(current_user)):
    for r in core.room_states(s):
        if r.id == room_id:
            return r
    raise HTTPException(404, "Oda bulunamadı")


@router.get("/{room_id}/series", response_model=list[SeriesPoint])
def series(room_id: str, metric: str = "co2_ppm", minutes: int = Query(60, ge=1, le=60 * 24 * 7),
           s: Session = Depends(get_session), _: User = Depends(current_user)):
    since = utcnow() - timedelta(minutes=minutes)
    q = (select(Reading).where(Reading.room_id == room_id, Reading.metric == metric, Reading.ts >= since)
         .order_by(Reading.ts).limit(2000))
    return [SeriesPoint(ts=r.ts, value=r.value) for r in s.exec(q).all()]


@router.get("/{room_id}/commands", response_model=list[CommandOut])
def room_commands(room_id: str, limit: int = 20, s: Session = Depends(get_session),
                  _: User = Depends(current_user)):
    q = select(Command).where(Command.room_id == room_id).order_by(col(Command.created_at).desc()).limit(limit)
    return s.exec(q).all()


@router.post("/{room_id}/commands", response_model=CommandOut, status_code=201)
def manual_command(room_id: str, body: ManualCommandIn, s: Session = Depends(get_session),
                   user: User = Depends(require_operator)):
    """Operatörün elle müdahalesi (ör. fanı aç). Agent kararlarıyla aynı yoldan cihaza gider."""
    core.audit(s, user.id, "command.manual", room_id, f"{body.actuator}={body.value}")
    return core.create_command(s, room_id, body.actuator, body.value, source_type="user",
                               source_id=user.id, reason=body.reason)
