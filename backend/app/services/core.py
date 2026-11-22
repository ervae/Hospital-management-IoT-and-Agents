"""Çekirdek iş mantığı. HTTP, MQTT ve simülatör hepsi buradaki fonksiyonları kullanır;
böylece veri nereden gelirse gelsin aynı kurallardan geçer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlmodel import Session, col, select

from ..models import (Agent, AuditLog, Command, CommandStatus, Decision, Device, LatestReading,
                      Reading, Room, RoomType, utcnow)
from ..schemas import ACTUATORS, DecisionOut, RoomState, SiteEnergy
from . import bus

SITE = "site"
OFFLINE_AFTER = timedelta(minutes=5)

DEFAULT_THRESHOLDS = {
    RoomType.icu: {"co2_warn": 800, "co2_crit": 1000, "pm25_warn": 12, "pm25_crit": 25},
    RoomType.patient: {"co2_warn": 1000, "co2_crit": 1400, "pm25_warn": 15, "pm25_crit": 35},
    RoomType.waiting: {"co2_warn": 1000, "co2_crit": 1500, "pm25_warn": 25, "pm25_crit": 50},
    RoomType.corridor: {"co2_warn": 1100, "co2_crit": 1600, "pm25_warn": 25, "pm25_crit": 50},
    RoomType.other: {"co2_warn": 1000, "co2_crit": 1500, "pm25_warn": 25, "pm25_crit": 50},
}


def _aware(ts: datetime | None) -> datetime | None:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def audit(s: Session, actor: str, action: str, target: str = "", detail: str = "") -> None:
    s.add(AuditLog(actor=actor, action=action, target=target, detail=detail))


def thresholds_for(room: Room) -> dict:
    return {**DEFAULT_THRESHOLDS.get(room.type, DEFAULT_THRESHOLDS[RoomType.other]),
            **(room.thresholds or {})}


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
    return count


def latest_by_scope(s: Session) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in s.exec(select(LatestReading)).all():
        d = out.setdefault(r.scope, {"metrics": {}, "updated_at": None})
        d["metrics"][r.metric] = r.value
        ts = _aware(r.ts)
        if d["updated_at"] is None or ts > d["updated_at"]:
            d["updated_at"] = ts
    return out


def room_status(room: Room, metrics: dict, updated_at: datetime | None) -> str:
    if updated_at is None or utcnow() - updated_at > OFFLINE_AFTER:
        return "offline"
    t = thresholds_for(room)
    co2, pm = metrics.get("co2_ppm"), metrics.get("pm25")
    if (co2 is not None and co2 >= t["co2_crit"]) or (pm is not None and pm >= t["pm25_crit"]):
        return "critical"
    if (co2 is not None and co2 >= t["co2_warn"]) or (pm is not None and pm >= t["pm25_warn"]):
        return "warning"
    return "ok"


def room_states(s: Session) -> list[RoomState]:
    latest = latest_by_scope(s)
    rooms = s.exec(select(Room).order_by(Room.priority, Room.code)).all()
    out = []
    for r in rooms:
        d = latest.get(r.id, {"metrics": {}, "updated_at": None})
        out.append(RoomState(**r.model_dump(), metrics=d["metrics"], updated_at=d["updated_at"],
                             status=room_status(r, d["metrics"], d["updated_at"])))
    return out


def site_energy(s: Session) -> SiteEnergy:
    d = latest_by_scope(s).get(SITE, {"metrics": {}, "updated_at": None})
    m = d["metrics"]
    return SiteEnergy(pv_kw=m.get("pv_kw"), load_kw=m.get("load_kw"), battery_pct=m.get("battery_pct"),
                      grid_kw=m.get("grid_kw"), sim_hour=m.get("sim_hour"), updated_at=d["updated_at"])


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
