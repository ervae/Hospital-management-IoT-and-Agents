"""MQTT köprüsü (isteğe bağlı, HASTANE_MQTT=1 ile açılır; `pip install paho-mqtt` gerekir).

Konu sözleşmesi:
  hastane/<device_id>/telemetry  (cihaz -> sunucu)  {"key": "<cihaz anahtarı>", "metrics": {...}}
  hastane/<device_id>/commands   (sunucu -> cihaz)  {"id": "cmd_..", "actuator": "led", "value": "ready"}
  hastane/<device_id>/ack        (cihaz -> sunucu)  {"key": "...", "id": "cmd_..", "status": "done"}
ESP32 HTTP de kullanabilir; ikisi aynı çekirdekten geçer.
"""
from __future__ import annotations

import asyncio
import json

from sqlmodel import Session

from ..config import settings
from ..db import get_engine
from ..models import Command, CommandStatus, Device, DeviceKind
from ..security import key_matches
from ..services import bus, core
from .base import Adapter


class MqttBridge(Adapter):
    name = "mqtt"

    async def run(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self.log.error("paho-mqtt kurulu değil; MQTT köprüsü kapalı. `pip install paho-mqtt`")
            return
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.on_message = self._on_message
        client.connect(settings.mqtt_host, settings.mqtt_port)
        client.subscribe("hastane/+/telemetry")
        client.subscribe("hastane/+/ack")
        self.client = client
        bus.subscribe("command.created", self._push_command)
        client.loop_start()
        self.log.info("MQTT bağlı: %s:%s", settings.mqtt_host, settings.mqtt_port)
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            client.loop_stop()

    def _on_message(self, _client, _ud, msg) -> None:
        try:
            _, device_id, kind = msg.topic.split("/")
            body = json.loads(msg.payload)
            with Session(get_engine()) as s:
                dev = s.get(Device, device_id)
                if not dev or not dev.active or not key_matches(body.get("key", ""), dev.key_hash):
                    self.log.warning("Yetkisiz MQTT mesajı: %s", msg.topic)
                    return
                if kind == "telemetry":
                    core.ingest(s, body.get("metrics", {}), dev.room_id, device_id=dev.id)
                elif kind == "ack":
                    cmd = s.get(Command, body.get("id"))
                    if cmd and cmd.device_id == dev.id:
                        core.ack_command(s, cmd, CommandStatus(body.get("status", "done")))
        except Exception:
            self.log.exception("MQTT mesajı işlenemedi: %s", msg.topic)

    def _push_command(self, payload: dict) -> None:
        with Session(get_engine()) as s:
            cmd = s.get(Command, payload["command_id"])
            if not cmd or not cmd.device_id:
                return
            dev = s.get(Device, cmd.device_id)
            if not dev or dev.kind != DeviceKind.physical:
                return
            self.client.publish(f"hastane/{dev.id}/commands", json.dumps(
                {"id": cmd.id, "actuator": cmd.actuator, "value": cmd.value}), qos=1)
            cmd.status = CommandStatus.delivered
            s.add(cmd)
            s.commit()
