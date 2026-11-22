"""Agent API'si. Yönetici agent kaydeder, sunucu kimlik + anahtar üretir.
Agent (Cengizhan'ın kodu) durumu okur, karar gönderir; kararın eylemleri komut olarak cihaza gider.
Agent hangi dilde/nerede çalışırsa çalışsın sadece bu HTTP sözleşmesini bilmesi yeterli."""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..deps import current_agent, current_user, require_admin
from ..models import Agent, User
from ..schemas import AgentCreate, AgentCreated, AgentOut, DecisionIn, DecisionOut, Overview
from ..security import hash_key, new_api_key
from ..services import core
from .overview import build_overview

router = APIRouter(prefix="/agents", tags=["Agent'lar"])


@router.get("", response_model=list[AgentOut])
def list_agents(s: Session = Depends(get_session), _: User = Depends(current_user)):
    return s.exec(select(Agent).order_by(Agent.name)).all()


@router.post("", response_model=AgentCreated, status_code=201)
def create_agent(body: AgentCreate, s: Session = Depends(get_session), admin: User = Depends(require_admin)):
    key = new_api_key("ak")
    ag = Agent(**body.model_dump(), key_hash=hash_key(key))
    s.add(ag)
    core.audit(s, admin.id, "agent.create", ag.id, body.name)
    s.commit()
    s.refresh(ag)
    return AgentCreated(**ag.model_dump(), api_key=key)


@router.patch("/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: str, body: dict, s: Session = Depends(get_session),
                 admin: User = Depends(require_admin)):
    ag = s.get(Agent, agent_id)
    if not ag:
        raise HTTPException(404, "Agent bulunamadı")
    for k in ("name", "description", "active"):
        if k in body:
            setattr(ag, k, body[k])
    s.add(ag)
    core.audit(s, admin.id, "agent.update", ag.id)
    s.commit()
    s.refresh(ag)
    return ag


# ---- Agent'ın kendisinin çağırdığı uçlar (X-Agent-Key başlığı ile) ----
@router.get("/me/state", response_model=Overview)
def agent_state(ag: Agent = Depends(current_agent), s: Session = Depends(get_session)):
    """Kat durumunun tamamı: odalar, son ölçümler, enerji, son kararlar."""
    s.commit()
    return build_overview(s)


@router.post("/me/decisions", response_model=DecisionOut, status_code=201)
def post_decision(body: DecisionIn, ag: Agent = Depends(current_agent), s: Session = Depends(get_session)):
    return core.record_decision(s, ag, body.summary, body.room_id, body.level,
                                [a.model_dump() for a in body.actions], body.data)
