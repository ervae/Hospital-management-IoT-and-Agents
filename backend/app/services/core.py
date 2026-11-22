"""Çekirdek iş mantığı. HTTP, MQTT ve simülatör hepsi buradaki fonksiyonları kullanır;
böylece veri nereden gelirse gelsin aynı kurallardan geçer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlmodel import Session, col, select

from ..models import (Agent, AuditLog, BedState, Command, CommandStatus, Decision, Device,
                      LatestReading, Reading, Room, RoomStatus, StateTransition, utcnow)
from ..schemas import ACTUATORS, STATE_TR, DecisionOut, RoomState
from . import bus, room_state

SITE = "site"
OFFLINE_AFTER = timedelta(minutes=5)
SIM = {"clock": None}  # simülatör açıksa simülasyon saati ("Gün 2 · 14:05")
STATE_METRICS = {"bed_load_kg", "bed_occupied", "motion", "door_open", "door_events"}


def _aware(ts: datetime | None) -> datetime | None:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def audit(s: Session, actor: str, action: str, target: str = "", detail: str = "") -> None:
    s.add(AuditLog(actor=actor, action=action, target=target, detail=detail))


# ---------------- Ölçümler ----------------
def ingest(s: Session, metrics: dict[str, float], room_id: str | None,
           device_id: str | None = None, ts: datetime | None = None) -> int:
    """Ölçümleri kaydeder ve son değer tablosunu günceller. room_id None ise bina kapsamı."""
    if room_id is not None and not s.get(Room, room_id):
        raise HTTPException(404, f"Oda bulunamadı: {room_id}")
    ts = _aware(ts) or utcnow()
    scope = room_id or SITE
    count = 0
    for metric, value in metrics.items():
        if not isinstance(value, (int, float)) or value != value:  # NaN filtrele
            continue
        metric = metric.strip().lower()[:40]
        s.add(Reading(ts=ts, room_id=room_id, device_id=device_id, metric=metric, value=float(value)))
        latest = s.exec(select(LatestReading).where(
            LatestReading.scope == scope, LatestReading.metric == metric)).first()
        if latest is None:
            latest = LatestReading(scope=scope, metric=metric, value=float(value), ts=ts, device_id=device_id)
        elif _aware(latest.ts) <= ts:
            latest.value, latest.ts, latest.device_id = float(value), ts, device_id
        s.add(latest)
        count += 1
    s.commit()
    bus.publish("reading", {"room_id": room_id, "device_id": device_id, "metrics": metrics, "ts": ts})
    if room_id is not None and STATE_METRICS & set(metrics):
        infer_state(s, room_id, metrics, ts, device_id)
    return count


# ---------------- Oda durumu ----------------
def get_status(s: Session, room_id: str) -> RoomStatus:
    st = s.get(RoomStatus, room_id)
    if st is None:
        st = RoomStatus(room_id=room_id)
        s.add(st)
    return st


def infer_state(s: Session, room_id: str, metrics: dict, ts: datetime, device_id: str | None) -> None:
    """Ölçümden oda durumunu çıkarır (room_state.step). Dış sınıflandırıcı kullanan oda atlanır."""
    room = s.get(Room, room_id)
    if room is None or (room.thresholds or {}).get("inference") == "external":
        return
    dev = s.get(Device, device_id) if device_id else None
    p = room_state.params(room.thresholds, dev.capabilities if dev else None)
    st = get_status(s, room_id)
    res = room_state.step(st.state, st.ctx or {}, metrics, ts, p)
    st.ctx = res.ctx
    set_state(s, room_id, res.state, res.confidence, "inference", ts, status=st)


def set_state(s: Session, room_id: str, state: BedState, confidence: float, source: str,
              ts: datetime | None = None, status: RoomStatus | None = None) -> RoomStatus:
    ts = _aware(ts) or utcnow()
    st = status or get_status(s, room_id)
    old = st.state
    st.confidence, st.source, st.updated_at = confidence, source, ts
    if old != state:
        st.state, st.since = state, ts
        s.add(StateTransition(room_id=room_id, ts=ts, from_state=old, to_state=state, source=source,
                              confidence=confidence, channel="iot"))
        if state == BedState.occupied and st.reserved_for:
            st.reserved_for = None  # rezerve edilen hasta yatağa geldi
    s.add(st)
    s.commit()
    if old != state:
        bus.publish("state", {"room_id": room_id, "from": old.value, "to": state.value, "ts": ts})
    return st


def set_his_state(s: Session, room_id: str, state: BedState, ts: datetime | None = None,
                  source: str = "manual") -> RoomStatus:
    """HBYS'deki (elle girilen) durumu günceller. IoT durumundan bağımsızdır."""
    ts = _aware(ts) or utcnow()
    st = get_status(s, room_id)
    if st.his_state != state:
        s.add(StateTransition(room_id=room_id, ts=ts, from_state=st.his_state, to_state=state,
                              source=source, confidence=1.0, channel="his"))
        st.his_state = state
    st.his_updated_at = ts
    s.add(st)
    s.commit()
    return st


def latest_by_scope(s: Session) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in s.exec(select(LatestReading)).all():
        d = out.setdefault(r.scope, {"metrics": {}, "updated_at": None})
        d["metrics"][r.metric] = r.value
        ts = _aware(r.ts)
        if d["updated_at"] is None or ts > d["updated_at"]:
            d["updated_at"] = ts
    return out


def is_online(updated_at: datetime | None) -> bool:
    return updated_at is not None and utcnow() - updated_at <= OFFLINE_AFTER


def room_states(s: Session) -> list[RoomState]:
    latest = latest_by_scope(s)
    statuses = {st.room_id: st for st in s.exec(select(RoomStatus)).all()}
    rooms = s.exec(select(Room).order_by(Room.ward, Room.code)).all()
    out = []
    for r in rooms:
        d = latest.get(r.id, {"metrics": {}, "updated_at": None})
        st = statuses.get(r.id) or RoomStatus(room_id=r.id)
        online = is_online(d["updated_at"])
        state = st.state
        # Sensör susarsa çıkarılan durum artık güvenilmez: BİLİNMİYOR
        if st.source in ("inference", "none") and not online:
            state = BedState.unknown
        out.append(RoomState(
            **r.model_dump(), metrics=d["metrics"], updated_at=d["updated_at"], state=state,
            state_label=STATE_TR[state.value], confidence=st.confidence if state == st.state else 0.0,
            state_source=st.source, state_since=_aware(st.since), his_state=st.his_state,
            his_updated_at=_aware(st.his_updated_at),
            hidden_free=state == BedState.ready and st.his_state != BedState.ready and not st.reserved_for,
            reserved_for=st.reserved_for, online=online))
    return out


# ---------------- Komutlar ----------------
def create_command(s: Session, room_id: str, actuator: str, value: float, *, source_type: str,
                   source_id: str | None, reason: str = "", decision_id: str | None = None,
                   commit: bool = True) -> Command:
    if actuator not in ACTUATORS:
        raise HTTPException(422, f"Bilinmeyen eyleyici: {actuator}. Geçerli: {', '.join(ACTUATORS)}")
    if not s.get(Room, room_id):
        raise HTTPException(404, f"Oda bulunamadı: {room_id}")
    # Odada bu eyleyiciyi destekleyen aktif cihazı bul (yoksa komut beklemede kalır)
    device = None
    for d in s.exec(select(Device).where(Device.room_id == room_id, Device.active == True)).all():  # noqa: E712
        if actuator in (d.capabilities or {}).get("actuators", []):
            device = d
            break
    cmd = Command(room_id=room_id, device_id=device.id if device else None, actuator=actuator,
                  value=max(0.0, min(100.0, float(value))), source_type=source_type,
                  source_id=source_id, reason=reason[:300], decision_id=decision_id)
    # Aynı eyleyiciye bekleyen eski komut varsa yenisi geçerli olsun
    for old in s.exec(select(Command).where(Command.room_id == room_id, Command.actuator == actuator,
                                            Command.status == CommandStatus.pending)).all():
        old.status, old.updated_at = CommandStatus.rejected, utcnow()
        old.reason = (old.reason + " [yeni komutla geçersiz]")[:300]
        s.add(old)
    s.add(cmd)
    if commit:
        s.commit()
        s.refresh(cmd)
        bus.publish("command.created", {"command_id": cmd.id})
    return cmd


def ack_command(s: Session, cmd: Command, status: CommandStatus, detail: str = "") -> Command:
    cmd.status, cmd.updated_at = status, utcnow()
    if detail:
        cmd.reason = (cmd.reason + f" | {detail}")[:300]
    s.add(cmd)
    s.commit()
    s.refresh(cmd)
    bus.publish("command.updated", {"command_id": cmd.id, "status": status.value})
    return cmd


# ---------------- Kararlar ----------------
def record_decision(s: Session, agent: Agent, summary: str, room_id: str | None, level: str,
                    actions: list[dict], data: dict) -> DecisionOut:
    if room_id and not s.get(Room, room_id):
        raise HTTPException(404, f"Oda bulunamadı: {room_id}")
    dec = Decision(agent_id=agent.id, room_id=room_id, summary=summary, level=level, data=data)
    s.add(dec)
    cmds = [create_command(s, a["room_id"], a["actuator"], a["value"], source_type="agent",
                           source_id=agent.id, reason=summary, decision_id=dec.id, commit=False)
            for a in actions]
    s.commit()
    for c in cmds:
        bus.publish("command.created", {"command_id": c.id})
    bus.publish("decision", {"decision_id": dec.id})
    return DecisionOut(**dec.model_dump(), agent_name=agent.name, agent_type=agent.agent_type,
                       command_ids=[c.id for c in cmds])


def recent_decisions(s: Session, limit: int = 20, room_id: str | None = None) -> list[DecisionOut]:
    q = select(Decision).order_by(col(Decision.created_at).desc()).limit(limit)
    if room_id:
        q = q.where(Decision.room_id == room_id)
    decs = s.exec(q).all()
    agents = {a.id: a for a in s.exec(select(Agent)).all()}
    out = []
    for d in decs:
        a = agents.get(d.agent_id)
        ids = [c.id for c in s.exec(select(Command).where(Command.decision_id == d.id)).all()]
        out.append(DecisionOut(**d.model_dump(), agent_name=a.name if a else None,
                               agent_type=a.agent_type if a else None, command_ids=ids))
    return out


def prune_old_readings(s: Session, days: int) -> None:
    cutoff = utcnow() - timedelta(days=days)
    for r in s.exec(select(Reading).where(Reading.ts < cutoff).limit(5000)).all():
        s.delete(r)
    s.commit()
