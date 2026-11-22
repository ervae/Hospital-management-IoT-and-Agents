"""Veritabanı tabloları.

Tasarım kuralları:
- Tüm kimlikler sunucuda üretilir (önekli rastgele id: usr_, room_, dev_, agt_, cmd_ ...).
- Ölçümler genel "metrik/değer" biçiminde tutulur: yeni bir sensör eklemek tablo değiştirmez.
- Oda bağımsız ölçümler (güneş paneli, batarya) room_id = None ile "bina" kapsamında tutulur.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


class Role(str, Enum):
    admin = "admin"        # kullanıcı/cihaz/agent yönetir
    operator = "operator"  # izler + elle komut verebilir
    viewer = "viewer"      # sadece izler


class RoomType(str, Enum):
    icu = "icu"              # yoğun bakım
    patient = "patient"      # hasta odası
    waiting = "waiting"      # bekleme salonu
    corridor = "corridor"    # koridor
    other = "other"


# Kritiklik önceliği: 1 en yüksek
DEFAULT_PRIORITY = {RoomType.icu: 1, RoomType.patient: 2, RoomType.waiting: 3,
                    RoomType.corridor: 4, RoomType.other: 5}


class User(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("usr"), primary_key=True)
    username: str = Field(index=True, unique=True)
    full_name: str = ""
    password_hash: str
    role: Role = Role.viewer
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class Room(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("room"), primary_key=True)
    code: str = Field(index=True, unique=True)   # örn. YB1, H301
    name: str
    type: RoomType = RoomType.other
    priority: int = 5
    floor: int = 3
    volume_m3: float = 45.0
    # Kat planındaki yer (0-100 arası oransal koordinat), arayüz çizimi için
    layout: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # Oda eşikleri (CO2 ppm, PM2.5 µg/m³) — odaya göre farklı olabilir
    thresholds: dict = Field(default_factory=dict, sa_column=Column(JSON))


class DeviceKind(str, Enum):
    physical = "physical"    # gerçek ESP32
    simulated = "simulated"  # simülatör


class Device(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("dev"), primary_key=True)
    name: str
    room_id: str | None = Field(default=None, foreign_key="room.id", index=True)
    kind: DeviceKind = DeviceKind.physical
    # Cihazın neyi ölçtüğü/sürdüğü, örn. {"sensors": ["co2_ppm"], "actuators": ["fan"]}
    capabilities: dict = Field(default_factory=dict, sa_column=Column(JSON))
    key_hash: str = ""
    active: bool = True
    last_seen: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Agent(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("agt"), primary_key=True)
    name: str
    agent_type: str = "custom"   # room | energy | coordinator | baseline | custom
    description: str = ""
    key_hash: str = ""
    active: bool = True
    builtin: bool = False
    last_seen: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Reading(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    room_id: str | None = Field(default=None, index=True)
    device_id: str | None = None
    metric: str = Field(index=True)
    value: float


class LatestReading(SQLModel, table=True):
    """Her (kapsam, metrik) için son değer. Arayüz hızlı okusun diye ayrı tutulur."""
    __table_args__ = (UniqueConstraint("scope", "metric"),)
    id: int | None = Field(default=None, primary_key=True)
    scope: str = Field(index=True)   # room_id veya "site"
    metric: str
    value: float
    ts: datetime = Field(default_factory=utcnow)
    device_id: str | None = None


class CommandStatus(str, Enum):
    pending = "pending"
    delivered = "delivered"
    done = "done"
    failed = "failed"
    rejected = "rejected"


class Command(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("cmd"), primary_key=True)
    room_id: str = Field(index=True)
    device_id: str | None = Field(default=None, index=True)
    actuator: str            # fan | purifier | light | ...
    value: float             # 0-100 (yüzde) veya 0/1
    status: CommandStatus = CommandStatus.pending
    source_type: str = "agent"   # agent | user | system
    source_id: str | None = None
    decision_id: str | None = None
    reason: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class Decision(SQLModel, table=True):
    """Bir agent'ın verdiği karar: neden + ürettiği komutlar. Arayüzdeki karar akışı buradan gelir."""
    id: str = Field(default_factory=lambda: new_id("dec"), primary_key=True)
    agent_id: str = Field(index=True)
    room_id: str | None = Field(default=None, index=True)
    summary: str
    level: str = "info"   # info | warning | critical
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow, index=True)


class AuditLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    actor: str
    action: str
    target: str = ""
    detail: str = ""
