from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, col, select

from ..db import get_session
from ..deps import current_user, require_admin, require_operator
from ..models import (DEFAULT_PRIORITY, Command, Reading, Room, StateLabel, StateTransition, User,
                      utcnow)
from ..schemas import (CommandOut, LabelIn, LabelOut, ManualCommandIn, RoomCreate, RoomOut, RoomState,
                       SeriesPoint, StateIn, TransitionOut)
from ..services import core

router = APIRouter(prefix="/rooms", tags=["Odalar"])


def _room(s: Session, room_id: str) -> Room:
    room = s.get(Room, room_id)
    if not room:
        raise HTTPException(404, "Oda bulunamadı")
    return room


@router.get("", response_model=list[RoomState])
def list_rooms(ward: str | None = None, s: Session = Depends(get_session), _: User = Depends(current_user)):
    rooms = core.room_states(s)
    return [r for r in rooms if ward is None or r.ward == ward]


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


@router.post("/{room_id}/state", response_model=RoomState)
def set_state(room_id: str, body: StateIn, s: Session = Depends(get_session),
              user: User = Depends(require_operator)):
    """Oda durumunu elle düzelt (operatör). Dış sınıflandırıcılar /devices/me/state kullanır."""
    _room(s, room_id)
    core.audit(s, user.id, "room.state.manual", room_id, body.state.value)
    core.set_state(s, room_id, body.state, body.confidence, "manual", body.ts)
    return get_room(room_id, s, user)


@router.post("/{room_id}/his-state", response_model=RoomState)
def set_his_state(room_id: str, body: StateIn, s: Session = Depends(get_session),
                  user: User = Depends(require_operator)):
    """HBYS'ye elle girilen durumu kaydet (gerçekte hemşirenin/temizlik personelinin girişi)."""
    _room(s, room_id)
    core.audit(s, user.id, "room.his_state", room_id, body.state.value)
    core.set_his_state(s, room_id, body.state, body.ts)
    return get_room(room_id, s, user)


@router.get("/{room_id}/transitions", response_model=list[TransitionOut])
def transitions(room_id: str, channel: str | None = Query(None, pattern="^(iot|his)$"),
                hours: int = Query(24, ge=1, le=24 * 60), s: Session = Depends(get_session),
                _: User = Depends(current_user)):
    """Durum geçişleri. Laboratuvar etiketleriyle karşılaştırılıp F1 ve tespit gecikmesi hesaplanır."""
    q = (select(StateTransition).where(StateTransition.room_id == room_id,
                                       StateTransition.ts >= utcnow() - timedelta(hours=hours))
         .order_by(StateTransition.ts).limit(5000))
    if channel:
        q = q.where(StateTransition.channel == channel)
    return s.exec(q).all()


@router.get("/{room_id}/labels", response_model=list[LabelOut])
def labels(room_id: str, hours: int = Query(24, ge=1, le=24 * 60), s: Session = Depends(get_session),
           _: User = Depends(current_user)):
    q = (select(StateLabel).where(StateLabel.room_id == room_id,
                                  StateLabel.ts >= utcnow() - timedelta(hours=hours))
         .order_by(StateLabel.ts).limit(5000))
    return s.exec(q).all()


@router.post("/{room_id}/labels", response_model=LabelOut, status_code=201)
def add_label(room_id: str, body: LabelIn, s: Session = Depends(get_session),
              user: User = Depends(require_operator)):
    """Laboratuvar kaydında gerçek durumu etiketle (ör. "temizlik personeli girdi" anında CLEANING)."""
    _room(s, room_id)
    lab = StateLabel(room_id=room_id, ts=core._aware(body.ts) or utcnow(), state=body.state,
                     note=body.note, labeled_by=user.id)
    s.add(lab)
    s.commit()
    s.refresh(lab)
    return lab


@router.get("/{room_id}/series", response_model=list[SeriesPoint])
def series(room_id: str, metric: str = "bed_load_kg", minutes: int = Query(60, ge=1, le=60 * 24 * 7),
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
    """Oda düğümüne elle komut (ör. kapıdaki durum LED'i)."""
    core.audit(s, user.id, "command.manual", room_id, f"{body.actuator}={body.value}")
    return core.create_command(s, room_id, body.actuator, body.value, source_type="user",
                               source_id=user.id, reason=body.reason)
