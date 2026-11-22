"""API sözleşmesi (v1). Cihazlar, agent'lar ve arayüz bu şemalarla konuşur.

Bu dosyadaki alanlar sadece EKLENEREK büyütülür; var olan alan silinmez/yeniden adlandırılmaz.
Böylece ESP32 yazılımı ve agent'lar sunucu güncellense de bozulmaz.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .models import CommandStatus, DeviceKind, Role, RoomType

# Bilinen metrikler (yeni metrik eklemek serbest; bunlar arayüzün tanıdıkları)
KNOWN_METRICS = {
    "co2_ppm": "CO2 (ppm)",
    "pm25": "PM2.5 (µg/m³)",
    "occupancy": "Doluluk (kişi)",
    "temp_c": "Sıcaklık (°C)",
    "humidity": "Nem (%)",
    "fan_pct": "Taze hava fanı (%)",
    "purifier_pct": "Hava temizleyici (%)",
    "light_pct": "Aydınlatma (%)",
    "power_w": "Oda gücü (W)",
    # bina kapsamı
    "pv_kw": "Güneş üretimi (kW)",
    "load_kw": "Toplam tüketim (kW)",
    "battery_pct": "Batarya (%)",
    "grid_kw": "Şebekeden çekilen (kW)",
}
ACTUATORS = {"fan": "fan_pct", "purifier": "purifier_pct", "light": "light_pct"}


# ---------- Kullanıcılar ----------
class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    id: str
    username: str
    full_name: str
    role: Role
    active: bool


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_.-]+$")
    full_name: str = ""
    role: Role = Role.viewer
    password: str | None = Field(default=None, min_length=8,
                                 description="Boş bırakılırsa sunucu geçici parola üretir")


class UserCreated(UserOut):
    temporary_password: str | None = None


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: Role | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


# ---------- Odalar ----------
class RoomOut(BaseModel):
    id: str
    code: str
    name: str
    type: RoomType
    priority: int
    floor: int
    volume_m3: float
    layout: dict
    thresholds: dict


class RoomCreate(BaseModel):
    code: str
    name: str
    type: RoomType = RoomType.other
    priority: int | None = None
    floor: int = 3
    volume_m3: float = 45.0
    layout: dict = {}
    thresholds: dict = {}


class RoomState(RoomOut):
    metrics: dict[str, float]
    updated_at: datetime | None
    status: str  # ok | warning | critical | offline


# ---------- Cihazlar ----------
class DeviceCreate(BaseModel):
    name: str
    room_id: str | None = None
    kind: DeviceKind = DeviceKind.physical
    capabilities: dict = {}


class DeviceOut(BaseModel):
    id: str
    name: str
    room_id: str | None
    kind: DeviceKind
    capabilities: dict
    active: bool
    last_seen: datetime | None


class DeviceCreated(DeviceOut):
    api_key: str = Field(description="Sadece bir kez gösterilir. ESP32 yazılımına girilir.")


class TelemetryIn(BaseModel):
    """ESP32 (veya simülatör) ölçüm gönderimi. Örn: {"metrics": {"co2_ppm": 812, "occupancy": 2}}"""
    metrics: dict[str, float]
    ts: datetime | None = None
    room_id: str | None = Field(default=None, description="Boşsa cihazın atandığı oda kullanılır")


class CommandOut(BaseModel):
    id: str
    room_id: str
    device_id: str | None
    actuator: str
    value: float
    status: CommandStatus
    source_type: str
    source_id: str | None
    reason: str
    created_at: datetime


class CommandAck(BaseModel):
    status: CommandStatus = CommandStatus.done
    detail: str = ""


# ---------- Agent'lar ----------
class AgentCreate(BaseModel):
    name: str
    agent_type: str = "custom"
    description: str = ""


class AgentOut(BaseModel):
    id: str
    name: str
    agent_type: str
    description: str
    active: bool
    builtin: bool
    last_seen: datetime | None


class AgentCreated(AgentOut):
    api_key: str


class ActionIn(BaseModel):
    room_id: str
    actuator: str = Field(description="fan | purifier | light")
    value: float = Field(ge=0, le=100)


class DecisionIn(BaseModel):
    """Agent kararı: bir açıklama ve isteğe bağlı eylemler. Eylemler komuta dönüşüp cihaza iletilir."""
    summary: str = Field(max_length=500)
    room_id: str | None = None
    level: str = Field(default="info", pattern="^(info|warning|critical)$")
    actions: list[ActionIn] = []
    data: dict = {}


class DecisionOut(BaseModel):
    id: str
    agent_id: str
    agent_name: str | None = None
    agent_type: str | None = None
    room_id: str | None
    summary: str
    level: str
    data: dict
    created_at: datetime
    command_ids: list[str] = []


class ManualCommandIn(BaseModel):
    actuator: str
    value: float = Field(ge=0, le=100)
    reason: str = "Elle müdahale"


# ---------- Genel görünüm ----------
class SiteEnergy(BaseModel):
    pv_kw: float | None = None
    load_kw: float | None = None
    battery_pct: float | None = None
    grid_kw: float | None = None
    sim_hour: float | None = None   # simülasyon saati (simülatör açıksa)
    updated_at: datetime | None = None


class Overview(BaseModel):
    rooms: list[RoomState]
    energy: SiteEnergy
    decisions: list[DecisionOut]
    alerts: int
    agents_online: int
    server_time: datetime


class SeriesPoint(BaseModel):
    ts: datetime
    value: float


TokenOut.model_rebuild()
