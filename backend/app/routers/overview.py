from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session
from ..deps import current_user
from ..models import Agent, AuditLog, User, utcnow
from ..schemas import KNOWN_METRICS, DecisionOut, Overview
from ..services import core, flow

router = APIRouter(tags=["Genel görünüm"])


def build_overview(s: Session) -> Overview:
    flow.revalidate_pending(s)
    rooms = core.room_states(s)
    now = utcnow()
    online = sum(1 for a in s.exec(select(Agent).where(Agent.active == True)).all()  # noqa: E712
                 if a.last_seen and now - core._aware(a.last_seen) < core.OFFLINE_AFTER)
    waiting = flow.open_requests(s)
    return Overview(rooms=rooms, kpis=flow.kpis(s, rooms, waiting, core.SIM["clock"],
                                                 settings.simulator_speed if core.SIM["clock"] else 1.0),
                    requests=[flow.request_out(r) for r in waiting],
                    recommendations=flow.recent_recommendations(s, 25),
                    decisions=core.recent_decisions(s, 15), agents_online=online, server_time=now)


@router.get("/overview", response_model=Overview)
def overview(s: Session = Depends(get_session), _: User = Depends(current_user)):
    """Ana ekranın tek çağrısı: odalar + göstergeler + bekleyen talepler + öneriler + ajan kayıtları."""
    return build_overview(s)


@router.get("/decisions", response_model=list[DecisionOut])
def decisions(limit: int = 50, room_id: str | None = None, s: Session = Depends(get_session),
              _: User = Depends(current_user)):
    return core.recent_decisions(s, min(limit, 200), room_id)


@router.get("/metrics")
def metrics(_: User = Depends(current_user)):
    return KNOWN_METRICS


@router.get("/audit")
def audit_log(limit: int = 100, s: Session = Depends(get_session), user: User = Depends(current_user)):
    from sqlmodel import col
    if user.role.value != "admin":
        return []
    return s.exec(select(AuditLog).order_by(col(AuditLog.ts).desc()).limit(min(limit, 500))).all()
