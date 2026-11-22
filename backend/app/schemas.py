"""API sözleşmesi (v1). Cihazlar, agent'lar ve arayüz bu şemalarla konuşur.

Bu dosyadaki alanlar sadece EKLENEREK büyütülür; var olan alan silinmez/yeniden adlandırılmaz.
Böylece ESP32 yazılımı ve agent'lar sunucu güncellense de bozulmaz.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .models import BedState, CommandStatus, DeviceKind, RecStatus, RequestStatus, Role, RoomType

# Bilinen metrikler (yeni metrik eklemek serbest; bunlar arayüzün ve çıkarım motorunun tanıdıkları)
KNOWN_METRICS = {
    "bed_load_kg": "Yatak yükü (kg)",       # yük hücresi + HX711
    "bed_occupied": "Yatakta hasta (0/1)",  # yük hücresi yerine FSR vb. kullanılırsa
    "motion": "Hareket (PIR tetik sayısı)",
    "door_open": "Kapı açık (0/1)",
    "door_events": "Kapı açılma sayısı",
    "temp_c": "Sıcaklık (°C)",
    "humidity": "Nem (%)",
    "co2_ppm": "CO2 (ppm, isteğe bağlı)",
    "rssi": "Wi-Fi sinyali (dBm)",
}
# Oda düğümündeki çıkışlar (isteğe bağlı): kapıdaki durum LED'i
ACTUATORS = {"led": "led_state"}

STATE_TR = {"occupied": "Dolu", "vacated": "Boşaldı – temizlik bekliyor", "cleaning": "Temizleniyor",
            "ready": "Hazır", "unknown": "Bilinmiyor"}


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
    ward: str
    type: RoomType
    priority: int
    floor: int
    beds: int
    sex: str
    isolation: bool
    layout: dict
    thresholds: dict


class RoomCreate(BaseModel):
    code: str
    name: str
    ward: str = "Genel"
    type: RoomType = RoomType.patient
    priority: int | None = None
    floor: int = 3
    beds: int = Field(default=1, ge=1, le=6)
    sex: str = Field(default="any", pattern="^(any|F|M)$")
    isolation: bool = False
    layout: dict = {}
    thresholds: dict = {}


class RoomState(RoomOut):
    metrics: dict[str, float]
    updated_at: datetime | None
    state: BedState
    state_label: str
    confidence: float
    state_source: str
    state_since: datetime | None
    his_state: BedState
    his_updated_at: datetime | None
    hidden_free: bool = Field(description="IoT'ye göre hazır ama HBYS'de hazır görünmüyor")
    reserved_for: str | None
    online: bool


class StateIn(BaseModel):
    """Dış sınıflandırıcı (ör. Erva'nın ML modeli) ya da operatör oda durumunu doğrudan bildirir."""
    state: BedState
    confidence: float = Field(default=1.0, ge=0, le=1)
    ts: datetime | None = None


class LabelIn(BaseModel):
    """Laboratuvar kaydı sırasında gerçek durum etiketi."""
    state: BedState
    ts: datetime | None = None
    note: str = ""


class LabelOut(BaseModel):
    id: int
    room_id: str
    ts: datetime
    state: BedState
    note: str
    labeled_by: str


class TransitionOut(BaseModel):
    id: int
    room_id: str
    ts: datetime
    from_state: BedState
    to_state: BedState
    source: str
    confidence: float
    channel: str


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
    """ESP32 (veya simülatör) ölçüm gönderimi. Örn: {"metrics": {"bed_load_kg": 71.5, "motion": 2, "door_open": 0}}"""
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
    actuator: str = Field(description="led")
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
    actuator: str = "led"
    value: float = Field(ge=0, le=100)
    reason: str = "Elle müdahale"


# ---------- Yatış talepleri ve öneriler ----------
class BedRequestIn(BaseModel):
    patient_ref: str = Field(default="", max_length=40, description="Sentetik kod; gerçek kimlik girilmez")
    ward: str
    sex: str = Field(default="any", pattern="^(any|F|M)$")
    isolation: bool = False
    urgency: int = Field(default=3, ge=1, le=5)
    origin: str = Field(default="ed", pattern="^(ed|elective|transfer)$")
    allow_overflow: bool = False
    note: str = Field(default="", max_length=300)


class BedRequestOut(BaseModel):
    id: str
    patient_ref: str
    ward: str
    sex: str
    isolation: bool
    urgency: int
    origin: str
    allow_overflow: bool
    note: str
    status: RequestStatus
    assigned_room_id: str | None
    created_at: datetime
    assigned_at: datetime | None
    waiting_min: float | None = None


class CandidateIn(BaseModel):
    room_id: str
    score: float | None = None
    reason: str = Field(default="", max_length=500)


class RecommendationIn(BaseModel):
    """Ajan önerisi: bir talep için en fazla 3 aday oda. Sunucu her adayı kısıt katmanından geçirir."""
    request_id: str
    candidates: list[CandidateIn] = Field(min_length=1, max_length=3)
    rationale: str = Field(default="", max_length=2000)
    data: dict = {}


class Candidate(BaseModel):
    room_id: str
    room_code: str | None = None
    rank: int
    score: float | None = None
    reason: str = ""
    violations: list[str] = []


class RecommendationOut(BaseModel):
    id: str
    request_id: str
    request: BedRequestOut | None = None
    agent_id: str
    agent_name: str | None = None
    agent_type: str | None = None
    candidates: list[Candidate]
    rationale: str
    data: dict
    status: RecStatus
    chosen_room_id: str | None
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str
    created_at: datetime


class ApproveIn(BaseModel):
    room_id: str | None = Field(default=None, description="Boşsa ilk geçerli aday")
    note: str = Field(default="", max_length=300)


class RejectIn(BaseModel):
    note: str = Field(default="", max_length=300)


# ---------- Genel görünüm ----------
class Kpis(BaseModel):
    ed_waiting: int = Field(description="Acil serviste yatak bekleyen hasta")
    avg_wait_min: float | None = Field(description="Bekleyenlerin ortalama bekleme süresi (dk)")
    max_wait_min: float | None
    ready_beds: int
    hidden_free: int = Field(description="IoT'ye göre hazır, HBYS'de hazır görünmeyen yatak")
    soon_ready: int = Field(description="Boşaldı ya da temizleniyor")
    occupied: int
    unknown: int
    total_rooms: int
    pending_recommendations: int
    sim_clock: str | None = None
    time_scale: float = Field(default=1.0, description="Simülasyon hızı; süreleri sim dakikasına çevirmek için")


class Overview(BaseModel):
    rooms: list[RoomState]
    kpis: Kpis
    requests: list[BedRequestOut]
    recommendations: list[RecommendationOut]
    decisions: list[DecisionOut]
    agents_online: int
    server_time: datetime


class SeriesPoint(BaseModel):
    ts: datetime
    value: float


TokenOut.model_rebuild()
