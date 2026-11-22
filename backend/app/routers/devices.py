"""Cihazlar. Yönetici cihazı kaydeder; sunucu cihaz kimliğini ve API anahtarını üretir.
ESP32 bu anahtarla ölçüm gönderir ve komut çeker (HTTP). MQTT adaptörü de aynı çekirdeği kullanır."""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, select

from ..db import get_session
from ..deps import current_device, current_user, require_admin
from ..models import Command, CommandStatus, Device, Room, User
from ..schemas import CommandAck, CommandOut, DeviceCreate, DeviceCreated, DeviceOut, StateIn, TelemetryIn
from ..security import hash_key, new_api_key
from ..services import core

router = APIRouter(prefix="/devices", tags=["Cihazlar"])


@router.get("", response_model=list[DeviceOut])
def list_devices(s: Session = Depends(get_session), _: User = Depends(current_user)):
    return s.exec(select(Device).order_by(Device.name)).all()


@router.post("", response_model=DeviceCreated, status_code=201)
def create_device(body: DeviceCreate, s: Session = Depends(get_session), admin: User = Depends(require_admin)):
    if body.room_id and not s.get(Room, body.room_id):
        raise HTTPException(404, "Oda bulunamadı")
    key = new_api_key("dk")
    dev = Device(**body.model_dump(), key_hash=hash_key(key))
    s.add(dev)
    core.audit(s, admin.id, "device.create", dev.id, body.name)
    s.commit()
    s.refresh(dev)
    return DeviceCreated(**dev.model_dump(), api_key=key)


@router.post("/{device_id}/rotate-key", response_model=DeviceCreated)
def rotate_key(device_id: str, s: Session = Depends(get_session), admin: User = Depends(require_admin)):
    dev = s.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "Cihaz bulunamadı")
    key = new_api_key("dk")
    dev.key_hash = hash_key(key)
    s.add(dev)
    core.audit(s, admin.id, "device.rotate_key", dev.id)
    s.commit()
    s.refresh(dev)
    return DeviceCreated(**dev.model_dump(), api_key=key)


@router.patch("/{device_id}", response_model=DeviceOut)
def update_device(device_id: str, body: dict, s: Session = Depends(get_session),
                  admin: User = Depends(require_admin)):
    dev = s.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "Cihaz bulunamadı")
    for k in ("name", "room_id", "active", "capabilities"):
        if k in body:
            setattr(dev, k, body[k])
    s.add(dev)
    core.audit(s, admin.id, "device.update", dev.id)
    s.commit()
    s.refresh(dev)
    return dev


# ---- Cihazın kendisinin çağırdığı uçlar (X-Device-Key başlığı ile) ----
@router.post("/me/telemetry", status_code=202)
def telemetry(body: TelemetryIn, dev: Device = Depends(current_device), s: Session = Depends(get_session)):
    room_id = body.room_id or dev.room_id
    if room_id is None and dev.capabilities.get("scope") != "site":
        raise HTTPException(400, "Cihaz bir odaya atanmamış")
    n = core.ingest(s, body.metrics, room_id, device_id=dev.id, ts=body.ts)
    return {"accepted": n}


@router.post("/me/state", status_code=202)
def device_state(body: StateIn, dev: Device = Depends(current_device), s: Session = Depends(get_session)):
    """Durumu cihazın kendisi (ya da dış sınıflandırıcı) hesaplıyorsa doğrudan bildirir."""
    if dev.room_id is None:
        raise HTTPException(400, "Cihaz bir odaya atanmamış")
    st = core.set_state(s, dev.room_id, body.state, body.confidence, "external", body.ts)
    return {"state": st.state.value}


@router.get("/me/commands", response_model=list[CommandOut])
def pending_commands(dev: Device = Depends(current_device), s: Session = Depends(get_session)):
    q = (select(Command).where(Command.status == CommandStatus.pending,
                               (Command.device_id == dev.id) | ((Command.device_id == None)  # noqa: E711
                                                                & (Command.room_id == dev.room_id)))
         .order_by(col(Command.created_at)))
    cmds = s.exec(q).all()
    supported = set(dev.capabilities.get("actuators", []))
    out = []
    for c in cmds:
        if c.actuator in supported:
            c.device_id, c.status = dev.id, CommandStatus.delivered
            s.add(c)
            out.append(c)
    s.commit()
    return out


@router.post("/me/commands/{command_id}/ack", response_model=CommandOut)
def ack(command_id: str, body: CommandAck, dev: Device = Depends(current_device),
        s: Session = Depends(get_session)):
    cmd = s.get(Command, command_id)
    if not cmd or cmd.device_id != dev.id:
        raise HTTPException(404, "Komut bulunamadı")
    return core.ack_command(s, cmd, body.status, body.detail)
