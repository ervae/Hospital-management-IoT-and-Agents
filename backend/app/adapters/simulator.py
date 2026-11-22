"""Sanal sensör üreteci ve basit hasta akışı (demo ve test için).

Her oda için gerçek bir oda düğümü gibi davranır: yük hücresi (bed_load_kg), PIR (motion),
kapı (door_events), sıcaklık ve nem üretir. Odanın gizli "gerçek" durumu şu döngüyü izler:

    dolu ──taburcu──► boşaldı ──temizlikçi gelir──► temizleniyor ──temizlik biter──► hazır
     ▲                                                                                │
     └──────────── onaylanan yatış ya da planlı yatış ile hasta gelir ◄──────────────┘

Sunucu bu ölçümlerden durumu kendisi çıkarır (services/room_state.py); simülatör yalnızca
gerçek durumu etiket olarak (StateLabel) kaydeder, böylece çıkarımın F1'i ölçülebilir.
HBYS'ye giriş gecikmeli yapılır (HASTANE_HIS_DELAY_MIN), aradaki fark "gizli boş yatak"tır.
Arızalar: HASTANE_SIM_DROP (veri kaybı) ve HASTANE_SIM_NOISE (hatalı okuma).

Not: Bitirme projesindeki asıl deney simülatörü (SimPy, 4 servis ~120 yatak) ayrı bir modüldür;
bu adaptör panelin canlı çalışmasını ve uçtan uca entegrasyonu göstermek içindir.
Gerçek ESP32 bir odaya bağlanınca simülatör o odayı otomatik bırakır.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from ..config import settings
from ..models import (BedRequest, BedState, Device, DeviceKind, RequestStatus, Room, RoomStatus,
                      StateLabel, utcnow)
from ..db import get_engine
from ..services import core, flow
from .base import Adapter

TZ = timezone(timedelta(hours=3))  # İstanbul
SENSORS = ["bed_load_kg", "motion", "door_events", "temp_c", "humidity"]


@dataclass
class RoomSim:
    room_id: str
    ward: str
    isolation: bool
    sex: str
    truth: BedState = BedState.ready
    until: float = 0.0          # mevcut aşamanın biteceği sim zamanı (sn)
    weight: float = 0.0
    temp: float = 22.5
    hum: float = 45.0
    incoming_at: float | None = None   # rezerve hastanın geleceği zaman


class Simulator(Adapter):
    name = "simulator"

    def __init__(self, seed: int | None = None) -> None:
        super().__init__()
        self.rng = random.Random(seed)
        self.rooms: dict[str, RoomSim] = {}
        self.devices: dict[str, str] = {}   # room_id -> sim cihaz id
        self.t = 0.0                        # sim zamanı (sn, başlangıçtan beri)
        self.t0 = datetime.now(TZ).replace(hour=8, minute=0, second=0, microsecond=0)
        self.his_queue: list[tuple[float, str, BedState]] = []
        self.patient_no = 1000

    # ---------- yardımcılar ----------
    def _h(self, hours: float) -> float:
        return hours * 3600

    def _los(self) -> float:
        """Yatış süresi: 1-5 gün arası, ortalama ~3 gün."""
        return self._h(self.rng.lognormvariate(4.1, 0.45))

    def clock(self) -> str:
        now = self.t0 + timedelta(seconds=self.t)
        return f"Gün {int(self.t // 86400) + 1} · {now:%H:%M}"

    def _label(self, s: Session, sim: RoomSim, state: BedState, note: str) -> None:
        sim.truth = state
        s.add(StateLabel(room_id=sim.room_id, ts=utcnow(), state=state, note=note, labeled_by="simulator"))

    def _his_later(self, sim: RoomSim, state: BedState, delay_s: float) -> None:
        self.his_queue.append((self.t + delay_s, sim.room_id, state))

    # ---------- kurulum ----------
    def _ensure_devices(self, s: Session) -> None:
        rooms = s.exec(select(Room)).all()
        physical = {d.room_id for d in s.exec(select(Device).where(
            Device.kind == DeviceKind.physical, Device.active == True)).all()}  # noqa: E712
        for r in rooms:
            dev = s.exec(select(Device).where(Device.room_id == r.id, Device.kind == DeviceKind.simulated)).first()
            if dev is None:
                dev = Device(name=f"SIM-{r.code}", room_id=r.id, kind=DeviceKind.simulated,
                             capabilities={"sensors": SENSORS, "actuators": [],
                                           "time_scale": settings.simulator_speed})
                s.add(dev)
            dev.capabilities = {**(dev.capabilities or {}), "time_scale": settings.simulator_speed}
            dev.active = r.id not in physical
            s.add(dev)
            if dev.active:
                self.devices[r.id] = dev.id
                if r.id not in self.rooms:
                    self.rooms[r.id] = self._initial(s, r)
            else:
                self.devices.pop(r.id, None)
                self.rooms.pop(r.id, None)
        s.commit()

    def _initial(self, s: Session, r: Room) -> RoomSim:
        sim = RoomSim(r.id, r.ward, r.isolation, r.sex, temp=self.rng.uniform(21.5, 23.5))
        x = self.rng.random()
        if x < 0.75:
            state, sim.until, sim.weight = BedState.occupied, self.rng.uniform(0, 3) * 86400, self.rng.uniform(55, 95)
        elif x < 0.85:
            state, sim.until = BedState.vacated, self.rng.uniform(5, 45) * 60
        elif x < 0.9:
            state, sim.until = BedState.cleaning, self.rng.uniform(10, 30) * 60
        else:
            state = BedState.ready
        self._label(s, sim, state, "başlangıç")
        core.set_his_state(s, r.id, state if state != BedState.cleaning else BedState.vacated, source="simulator")
        return sim

    # ---------- gerçek durum makinesi ----------
    def _advance(self, s: Session, sim: RoomSim, reserved: str | None) -> int:
        """Gerçek durumu ilerletir; bu adımdaki kapı olayı sayısını döndürür."""
        door = 0
        if sim.truth == BedState.occupied and self.t >= sim.until:
            self._label(s, sim, BedState.vacated, "taburcu")
            sim.weight, door = 0.0, 1
            sim.until = self.t + self.rng.uniform(15, 60) * 60      # temizlikçinin gelmesi
            self._his_later(sim, BedState.vacated, self.rng.uniform(0, 20) * 60)
        elif sim.truth == BedState.vacated and self.t >= sim.until:
            self._label(s, sim, BedState.cleaning, "temizlik başladı")
            door = 1
            sim.until = self.t + self.rng.uniform(20, 40) * 60
        elif sim.truth == BedState.cleaning and self.t >= sim.until:
            self._label(s, sim, BedState.ready, "temizlik bitti")
            door = 1
            # HBYS gecikmesi: ortalama his_delay_min, üstel dağılım
            self._his_later(sim, BedState.ready, self.rng.expovariate(1 / max(settings.his_delay_min, 1)) * 60)
        elif sim.truth == BedState.ready and reserved:
            if sim.incoming_at is None:
                sim.incoming_at = self.t + self.rng.uniform(5, 20) * 60
            elif self.t >= sim.incoming_at:
                self._admit(s, sim, "onaylanan yatış")
                door = 1
        return door

    def _admit(self, s: Session, sim: RoomSim, note: str) -> None:
        self._label(s, sim, BedState.occupied, note)
        sim.weight, sim.incoming_at = self.rng.uniform(50, 100), None
        sim.until = self.t + self._los()
        core.set_his_state(s, sim.room_id, BedState.occupied, source="simulator")

    def _sensors(self, sim: RoomSim, door: int, dt: float) -> dict[str, float]:
        mins = dt / 60
        if sim.truth == BedState.occupied:
            load = sim.weight + self.rng.gauss(0, 1.5)
            motion = self.rng.random() < 0.15 * mins and 1 or 0     # hasta ara sıra hareket eder
        elif sim.truth == BedState.cleaning:
            load = abs(self.rng.gauss(0, 0.8)) + (self.rng.random() < 0.2) * self.rng.uniform(2, 8)  # yatağa dokunma
            motion = self.rng.randint(3, 6)
        else:
            load = abs(self.rng.gauss(0, 0.6))
            motion = 0
        sim.temp += (22.5 + (0.4 if sim.truth == BedState.occupied else 0) - sim.temp) * 0.05 + self.rng.gauss(0, 0.03)
        sim.hum += (45 - sim.hum) * 0.05 + self.rng.gauss(0, 0.2)
        m = {"bed_load_kg": round(max(0.0, load), 1), "motion": motion, "door_events": door,
             "temp_c": round(sim.temp, 2), "humidity": round(sim.hum, 1)}
        if settings.sim_noise and self.rng.random() < settings.sim_noise:  # hatalı okuma
            if self.rng.random() < 0.5:
                m["bed_load_kg"] = round(self.rng.choice([0.0, self.rng.uniform(40, 90)]), 1)
            else:
                m["motion"] = self.rng.randint(0, 6)
        return m

    # ---------- hasta gelişi ----------
    def _arrivals(self, s: Session, dt: float) -> None:
        n = len(self.rooms) or 1
        per_s = settings.sim_ed_rate * n / 86400
        wards = sorted({r.ward for r in self.rooms.values()}) or ["Dahiliye"]
        # Acil servisten yatış talebi
        if self.rng.random() < per_s * dt:
            self.patient_no += 1
            urg = self.rng.choices([1, 2, 3, 4, 5], [0.05, 0.25, 0.4, 0.2, 0.1])[0]
            flow.create_request(s, {"patient_ref": f"H-{self.patient_no}", "ward": self.rng.choice(wards),
                                    "sex": self.rng.choice(["F", "M"]), "isolation": self.rng.random() < 0.08,
                                    "urgency": urg, "origin": "ed"}, "simulator")
        # Planlı yatış: doğrudan hazır bir odaya (onay akışına girmez)
        if self.rng.random() < per_s * dt:
            ready = [sim for sim in self.rooms.values() if sim.truth == BedState.ready and sim.incoming_at is None
                     and not (s.get(RoomStatus, sim.room_id) and s.get(RoomStatus, sim.room_id).reserved_for)]
            if ready:
                self._admit(s, self.rng.choice(ready), "planlı yatış")

    # ---------- döngü ----------
    def tick(self, s: Session) -> None:
        dt = settings.simulator_interval_s * settings.simulator_speed
        self._ensure_devices(s)
        self.t += dt
        core.SIM["clock"] = self.clock()
        reserved = {st.room_id: st.reserved_for for st in s.exec(select(RoomStatus)).all()}
        for room_id, sim in list(self.rooms.items()):
            door = self._advance(s, sim, reserved.get(room_id))
            m = self._sensors(sim, door, dt)
            if settings.sim_drop and self.rng.random() < settings.sim_drop:
                continue  # veri kaybı
            core.ingest(s, m, room_id, device_id=self.devices[room_id])
        due = [x for x in self.his_queue if x[0] <= self.t]
        self.his_queue = [x for x in self.his_queue if x[0] > self.t]
        for _, room_id, state in due:
            if room_id in self.rooms:
                core.set_his_state(s, room_id, state, source="simulator")
        self._arrivals(s, dt)
        # İptal: çok uzun bekleyen talepler (gerçekte başka hastaneye sevk)
        for req in s.exec(select(BedRequest).where(BedRequest.status == RequestStatus.waiting)).all():
            if (flow.wait_min(req) or 0) * settings.simulator_speed > 48 * 60:
                flow.cancel_request(s, req, "simulator")
        s.commit()

    def _tick_once(self) -> None:
        with Session(get_engine()) as s:
            self.tick(s)

    async def run(self) -> None:
        self.log.info("Simülatör başladı (%.0fx hız)", settings.simulator_speed)
        while True:
            await asyncio.to_thread(self._tick_once)
            await asyncio.sleep(settings.simulator_interval_s)
