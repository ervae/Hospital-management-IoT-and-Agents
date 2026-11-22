"""Hasta akışı: yatış talepleri, ajan önerileri, kısıt denetimi ve insan onayı.

Kural: hiçbir ajan yatağı doğrudan atamaz. Ajan öneri gönderir, sunucu her adayı kısıt
katmanından geçirir, yatak yönetim sorumlusu panelden onaylar ya da reddeder. Her adım
denetim izine (AuditLog) yazılır.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlmodel import Session, col, select

from ..models import (Agent, BedRequest, BedState, Recommendation, RecStatus, RequestStatus, Room,
                      RoomStatus, utcnow)
from ..schemas import STATE_TR, BedRequestOut, Candidate, Kpis, RecommendationOut, RoomState
from . import bus, core


# ---------------- Kısıt katmanı ----------------
def check_candidate(room: Room | None, st: RoomStatus | None, req: BedRequest,
                    effective_state: BedState | None = None) -> list[str]:
    """Deterministik klinik/operasyonel kurallar. Boş liste = aday geçerli.
    Cengizhan'ın kısıt katmanı daha zengin kurallar ekleyebilir; sunucu bu temel kuralları her zaman uygular."""
    if room is None:
        return ["Oda bulunamadı"]
    v: list[str] = []
    state = effective_state or (st.state if st else BedState.unknown)
    if state != BedState.ready:
        v.append(f"Yatak hazır değil ({STATE_TR[state.value].lower()})")
    if st and st.reserved_for and st.reserved_for != req.id:
        v.append("Yatak başka bir hastaya ayrılmış")
    if room.ward != req.ward and not req.allow_overflow:
        v.append(f"Branş uyumsuz: talep {req.ward}, oda {room.ward}")
    if req.sex in ("F", "M") and room.sex not in ("any", req.sex):
        v.append("Cinsiyet kuralı: oda bu hastaya uygun değil")
    if req.isolation and not room.isolation:
        v.append("İzolasyon gerekli ama oda izolasyon odası değil")
    return v


def _effective_states(s: Session) -> dict[str, RoomState]:
    return {r.id: r for r in core.room_states(s)}


# ---------------- Talepler ----------------
def wait_min(req: BedRequest, now: datetime | None = None) -> float | None:
    now = now or utcnow()
    end = core._aware(req.assigned_at) if req.assigned_at else now
    return round((end - core._aware(req.created_at)).total_seconds() / 60, 1)


def request_out(req: BedRequest) -> BedRequestOut:
    return BedRequestOut(**req.model_dump(), waiting_min=wait_min(req))


def create_request(s: Session, data: dict, actor: str) -> BedRequest:
    req = BedRequest(**data)
    s.add(req)
    core.audit(s, actor, "request.create", req.id, f"{req.ward} aciliyet {req.urgency}")
    s.commit()
    s.refresh(req)
    bus.publish("request", {"request_id": req.id})
    return req


def open_requests(s: Session) -> list[BedRequest]:
    q = (select(BedRequest).where(BedRequest.status == RequestStatus.waiting)
         .order_by(BedRequest.urgency, BedRequest.created_at))
    return list(s.exec(q).all())


def cancel_request(s: Session, req: BedRequest, actor: str) -> BedRequest:
    if req.status != RequestStatus.waiting:
        raise HTTPException(409, "Talep zaten kapanmış")
    req.status = RequestStatus.cancelled
    _supersede(s, req.id)
    core.audit(s, actor, "request.cancel", req.id)
    s.add(req)
    s.commit()
    return req


def _supersede(s: Session, request_id: str, keep: str | None = None) -> None:
    for r in s.exec(select(Recommendation).where(Recommendation.request_id == request_id,
                                                 Recommendation.status == RecStatus.pending)).all():
        if r.id != keep:
            r.status = RecStatus.superseded
            s.add(r)


# ---------------- Öneriler ----------------
def rec_out(s: Session, rec: Recommendation, agents: dict[str, Agent] | None = None) -> RecommendationOut:
    a = (agents or {}).get(rec.agent_id) or s.get(Agent, rec.agent_id)
    codes = {r.id: r.code for r in s.exec(select(Room)).all()}
    cands = [Candidate(**c, room_code=codes.get(c["room_id"])) for c in rec.candidates]
    req = s.get(BedRequest, rec.request_id)
    return RecommendationOut(**{k: v for k, v in rec.model_dump().items() if k != "candidates"},
                             candidates=cands, request=request_out(req) if req else None, agent_name=a.name if a else None,
                             agent_type=a.agent_type if a else None)


def submit_recommendation(s: Session, agent: Agent, request_id: str, candidates: list[dict],
                          rationale: str, data: dict) -> RecommendationOut:
    req = s.get(BedRequest, request_id)
    if req is None:
        raise HTTPException(404, "Talep bulunamadı")
    if req.status != RequestStatus.waiting:
        raise HTTPException(409, "Talep artık yatak beklemiyor")
    states = _effective_states(s)
    checked = []
    for i, c in enumerate(candidates[:3]):
        room = s.get(Room, c["room_id"])
        eff = states.get(c["room_id"])
        v = check_candidate(room, s.get(RoomStatus, c["room_id"]), req, eff.state if eff else None)
        checked.append({"room_id": c["room_id"], "rank": i + 1, "score": c.get("score"),
                        "reason": c.get("reason", ""), "violations": v})
    status = RecStatus.pending if any(not c["violations"] for c in checked) else RecStatus.blocked
    rec = Recommendation(request_id=req.id, agent_id=agent.id, candidates=checked,
                         rationale=rationale, data=data, status=status)
    s.add(rec)
    if status == RecStatus.pending:
        _supersede(s, req.id, keep=rec.id)
    core.audit(s, agent.id, "recommendation.create", rec.id,
               f"{req.id} → {', '.join(c['room_id'] for c in checked)} ({status.value})")
    s.commit()
    s.refresh(rec)
    bus.publish("recommendation", {"recommendation_id": rec.id, "status": status.value})
    return rec_out(s, rec, {agent.id: agent})


def approve(s: Session, rec: Recommendation, room_id: str | None, note: str, actor: str) -> RecommendationOut:
    if rec.status != RecStatus.pending:
        raise HTTPException(409, f"Öneri onaylanamaz (durum: {rec.status.value})")
    req = s.get(BedRequest, rec.request_id)
    if req is None or req.status != RequestStatus.waiting:
        raise HTTPException(409, "Talep artık yatak beklemiyor")
    valid = [c for c in rec.candidates if not c["violations"]]
    if room_id is None:
        if not valid:
            raise HTTPException(422, "Geçerli aday yok")
        room_id = valid[0]["room_id"]
    if room_id not in {c["room_id"] for c in rec.candidates}:
        raise HTTPException(422, "Bu oda öneride yok")
    # Onay anında kuralları yeniden denetle: öneri geldikten sonra durum değişmiş olabilir
    eff = _effective_states(s).get(room_id)
    v = check_candidate(s.get(Room, room_id), s.get(RoomStatus, room_id), req, eff.state if eff else None)
    if v:
        raise HTTPException(409, "Kısıt ihlali: " + "; ".join(v))
    now = utcnow()
    st = core.get_status(s, room_id)
    st.reserved_for = req.id
    s.add(st)
    req.status, req.assigned_room_id, req.assigned_at = RequestStatus.assigned, room_id, now
    rec.status, rec.chosen_room_id, rec.decided_by, rec.decided_at, rec.decision_note = (
        RecStatus.approved, room_id, actor, now, note)
    s.add(req)
    s.add(rec)
    _supersede(s, req.id, keep=rec.id)
    core.audit(s, actor, "recommendation.approve", rec.id, f"{req.id} → {room_id} {note}".strip())
    s.commit()
    s.refresh(rec)
    bus.publish("assignment", {"request_id": req.id, "room_id": room_id})
    return rec_out(s, rec)


def reject(s: Session, rec: Recommendation, note: str, actor: str) -> RecommendationOut:
    if rec.status not in (RecStatus.pending, RecStatus.blocked):
        raise HTTPException(409, f"Öneri reddedilemez (durum: {rec.status.value})")
    rec.status, rec.decided_by, rec.decided_at, rec.decision_note = RecStatus.rejected, actor, utcnow(), note
    s.add(rec)
    core.audit(s, actor, "recommendation.reject", rec.id, note)
    s.commit()
    s.refresh(rec)
    bus.publish("recommendation", {"recommendation_id": rec.id, "status": "rejected"})
    return rec_out(s, rec)


def revalidate_pending(s: Session) -> int:
    """Bekleyen önerilerin adaylarını yeniden denetler (öneri geldikten sonra oda durumu değişmiş olabilir).
    Hiç geçerli aday kalmadıysa öneri "superseded" olur ve ajan yeniden önerir. Değişen öneri sayısını döndürür."""
    pending = s.exec(select(Recommendation).where(Recommendation.status == RecStatus.pending)).all()
    if not pending:
        return 0
    states = _effective_states(s)
    changed = 0
    for rec in pending:
        req = s.get(BedRequest, rec.request_id)
        if req is None or req.status != RequestStatus.waiting:
            rec.status = RecStatus.superseded
        else:
            new = []
            for c in rec.candidates:
                eff = states.get(c["room_id"])
                v = check_candidate(s.get(Room, c["room_id"]), s.get(RoomStatus, c["room_id"]), req,
                                    eff.state if eff else None)
                new.append({**c, "violations": v})
            if new == rec.candidates:
                continue
            rec.candidates = new
            if all(c["violations"] for c in new):
                rec.status = RecStatus.superseded
                rec.decision_note = "Adaylar artık uygun değil"
        s.add(rec)
        changed += 1
    if changed:
        s.commit()
    return changed


def recent_recommendations(s: Session, limit: int = 20, status: RecStatus | None = None) -> list[RecommendationOut]:
    q = select(Recommendation).order_by(col(Recommendation.created_at).desc()).limit(limit)
    if status:
        q = q.where(Recommendation.status == status)
    agents = {a.id: a for a in s.exec(select(Agent)).all()}
    return [rec_out(s, r, agents) for r in s.exec(q).all()]


# ---------------- Göstergeler ----------------
def kpis(s: Session, rooms: list[RoomState], waiting: list[BedRequest], sim_clock: str | None = None,
         time_scale: float = 1.0) -> Kpis:
    ed = [r for r in waiting if r.origin == "ed"]
    waits = [wait_min(r) for r in ed]
    pending = s.exec(select(Recommendation).where(Recommendation.status == RecStatus.pending)).all()
    count = lambda *states: sum(1 for r in rooms if r.state in states)  # noqa: E731
    return Kpis(ed_waiting=len(ed), avg_wait_min=round(sum(waits) / len(waits), 1) if waits else None,
                max_wait_min=max(waits) if waits else None,
                ready_beds=sum(1 for r in rooms if r.state == BedState.ready and not r.reserved_for),
                hidden_free=sum(1 for r in rooms if r.hidden_free),
                soon_ready=count(BedState.vacated, BedState.cleaning), occupied=count(BedState.occupied),
                unknown=count(BedState.unknown), total_rooms=len(rooms),
                pending_recommendations=len(pending), sim_clock=sim_clock, time_scale=time_scale)
