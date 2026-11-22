"""Veritabanı tabloları.

Tasarım kuralları:
- Tüm kimlikler sunucuda üretilir (önekli rastgele id: usr_, room_, dev_, agt_, req_, rec_ ...).
- Ölçümler genel "metrik/değer" biçiminde tutulur: yeni bir sensör eklemek tablo değiştirmez.
- Oda durumu (dolu / boşaldı / temizleniyor / hazır / bilinmiyor) ölçümlerden ayrı tutulur;
  her değişiklik StateTransition'a yazılır (F1 ve gecikme analizi için).
- HBYS'de görünen durum (his_state) ayrı alandır: IoT ile HBYS arasındaki fark "gizli boş yatak"tır.
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
    patient = "patient"      # hasta odası
    isolation = "isolation"  # izolasyon odası
    icu = "icu"              # yoğun bakım
    other = "other"


DEFAULT_PRIORITY = {RoomType.icu: 1, RoomType.isolation: 2, RoomType.patient: 3, RoomType.other: 5}


class BedState(str, Enum):
    """Oda durumları (docs/interface.md ile aynı)."""
    occupied = "occupied"    # DOLU: yatakta ağırlık var
    vacated = "vacated"      # BOŞALDI – TEMİZLİK BEKLİYOR
    cleaning = "cleaning"    # TEMİZLENİYOR
    ready = "ready"          # HAZIR: yeni hastaya atanabilir
    unknown = "unknown"      # BİLİNMİYOR: sensör verisi yok


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
    code: str = Field(index=True, unique=True)   # örn. DH-301
    name: str
    ward: str = Field(default="Genel", index=True)  # servis / branş: Dahiliye, Kardiyoloji ...
    type: RoomType = RoomType.patient
    priority: int = 3
    floor: int = 3
    beds: int = 1
    sex: str = "any"          # any | F | M  (odaya kabul edilebilecek cinsiyet)
    isolation: bool = False   # izolasyon gerektiren hastayı kabul edebilir mi
    # Arayüz çizimi için serbest alan (sıra, konum)
    layout: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # Durum çıkarımı ayarları (ör. {"load_kg": 20, "quiet_s": 600}); boşsa varsayılanlar
    thresholds: dict = Field(default_factory=dict, sa_column=Column(JSON))


class RoomStatus(SQLModel, table=True):
    """Odanın şu anki durumu. IoT çıkarımı, dış sınıflandırıcı ya da elle güncellenir."""
    room_id: str = Field(primary_key=True, foreign_key="room.id")
    state: BedState = BedState.unknown
    confidence: float = 0.0
    source: str = "none"          # inference | external | manual | none
    since: datetime | None = None
    updated_at: datetime | None = None
    # HBYS'de görünen durum (elle / gecikmeli giriş). Fark = gizli boş yatak
    his_state: BedState = BedState.unknown
    his_updated_at: datetime | None = None
    reserved_for: str | None = None   # onaylanan yatış talebi (req_...)
    # Çıkarım motorunun iç hafızası (son hareket zamanı vb.)
    ctx: dict = Field(default_factory=dict, sa_column=Column(JSON))


class StateTransition(SQLModel, table=True):
    """Her durum değişikliği. Laboratuvar etiketleriyle karşılaştırılıp F1/gecikme hesaplanır."""
    id: int | None = Field(default=None, primary_key=True)
    room_id: str = Field(index=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    from_state: BedState
    to_state: BedState
    source: str = "inference"
    confidence: float = 0.0
    channel: str = "iot"   # iot | his


class StateLabel(SQLModel, table=True):
    """Gerçek durum etiketi (laboratuvarda elle ya da simülatörde otomatik). Değerlendirme için."""
    id: int | None = Field(default=None, primary_key=True)
    room_id: str = Field(index=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    state: BedState
    note: str = ""
    labeled_by: str = ""   # usr_... | simulator


class RequestStatus(str, Enum):
    waiting = "waiting"      # yatak bekliyor
    assigned = "assigned"    # yatak onaylandı
    cancelled = "cancelled"


class BedRequest(SQLModel, table=True):
    """Yatış talebi (çoğunlukla acil servisten). Gerçek hasta verisi tutulmaz: patient_ref sentetiktir."""
    id: str = Field(default_factory=lambda: new_id("req"), primary_key=True)
    patient_ref: str = ""            # sentetik kod, ör. H-1042
    ward: str = Field(index=True)    # istenen servis / branş
    sex: str = "any"                 # F | M | any
    isolation: bool = False
    urgency: int = 3                 # 1 en acil … 5
    origin: str = "ed"               # ed (acil) | elective | transfer
    allow_overflow: bool = False     # başka servise yatabilir mi
    note: str = ""
    status: RequestStatus = RequestStatus.waiting
    assigned_room_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
    assigned_at: datetime | None = None


class RecStatus(str, Enum):
    pending = "pending"        # insan onayı bekliyor
    approved = "approved"
    rejected = "rejected"
    blocked = "blocked"        # tüm adaylar kısıt ihlali içeriyor
    superseded = "superseded"  # aynı talep için yeni öneri geldi ya da talep kapandı


class Recommendation(SQLModel, table=True):
    """Ajanın yatak atama önerisi: en fazla 3 aday + gerekçe. Son karar insandadır."""
    id: str = Field(default_factory=lambda: new_id("rec"), primary_key=True)
    request_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    # [{"room_id", "rank", "score", "reason", "violations": [..]}]
    candidates: list = Field(default_factory=list, sa_column=Column(JSON))
    rationale: str = ""
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: RecStatus = RecStatus.pending
    chosen_room_id: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)


class DeviceKind(str, Enum):
    physical = "physical"    # gerçek ESP32
    simulated = "simulated"  # simülatör


class Device(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("dev"), primary_key=True)
    name: str
    room_id: str | None = Field(default=None, foreign_key="room.id", index=True)
    kind: DeviceKind = DeviceKind.physical
    # Cihazın neyi ölçtüğü/sürdüğü, örn. {"sensors": ["bed_load_kg", "motion"], "actuators": ["led"]}
    capabilities: dict = Field(default_factory=dict, sa_column=Column(JSON))
    key_hash: str = ""
    active: bool = True
    last_seen: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Agent(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("agt"), primary_key=True)
    name: str
    agent_type: str = "custom"   # ed | ward | cleaning | coordinator | single | baseline | custom
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
    scope: str = Field(index=True)   # room_id veya "site" (oda dışı ölçüm)
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
    actuator: str            # led | buzzer | ... (oda düğümündeki çıkışlar)
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
