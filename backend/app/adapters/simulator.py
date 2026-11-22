"""Hastane katı simülatörü.

Her oda için sahte bir cihaz gibi davranır: ölçüm üretir (CO2 kütle dengesi, PM2.5, doluluk),
kendisine gelen komutları (fan/temizleyici/ışık) uygular ve onaylar. Ayrıca güneş paneli + batarya
simüle eder. Gerçek ESP32 bir odaya bağlanınca simülatör o odayı otomatik bırakır.

Simülasyon saati hızlandırılmıştır (varsayılan 20x: bir gün ~72 dakika).
"""
from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine
from ..models import Command, CommandStatus, Device, DeviceKind, Room, RoomType
from ..services import core
from .base import Adapter

CO2_OUT = 420.0          # dış hava ppm
CO2_GEN = 0.0052         # kişi başı CO2 üretimi, L/s (oturan/dinlenen yetişkin)
TZ = timezone(timedelta(hours=3))  # İstanbul

VACANT_ROOMS = {"H304"}  # demo: boş hasta odası (enerji tasarrufu gösterimi)
MAX_OCC = {RoomType.icu: 3, RoomType.patient: 3, RoomType.waiting: 25, RoomType.corridor: 8,
           RoomType.other: 4}


@dataclass
class RoomSim:
    room_id: str
    rtype: RoomType
    volume: float
    co2: float = 600.0
    pm25: float = 10.0
    occ: int = 0
    fan: float = 20.0
    purifier: float = 0.0
    light: float = 0.0
    temp: float = 22.5
    hum: float = 45.0
    extra: dict = field(default_factory=dict)


class Simulator(Adapter):
    name = "simulator"

    def __init__(self) -> None:
        super().__init__()
        self.rooms: dict[str, RoomSim] = {}
        self.devices: dict[str, str] = {}  # room_id -> sim device id
        self.sim_time = datetime.now(TZ)
        self.battery_kwh = 6.0
        self.capacity_kwh = 10.0
        self.pv_peak_kw = 5.0
        self.cloud = 1.0
        self.rng = random.Random()

    # ---------- kurulum ----------
    def _ensure_devices(self, s: Session) -> None:
        rooms = s.exec(select(Room)).all()
        physical_rooms = {d.room_id for d in s.exec(select(Device).where(
            Device.kind == DeviceKind.physical, Device.active == True)).all()}  # noqa: E712
        for r in rooms:
            dev = s.exec(select(Device).where(Device.room_id == r.id, Device.kind == DeviceKind.simulated)).first()
            if dev is None:
                dev = Device(name=f"SIM-{r.code}", room_id=r.id, kind=DeviceKind.simulated,
                             capabilities={"sensors": ["co2_ppm", "pm25", "occupancy", "temp_c", "humidity"],
                                           "actuators": ["fan", "purifier", "light"]})
                s.add(dev)
            # Gerçek cihaz varsa simülasyon o odada durur
            dev.active = r.id not in physical_rooms
            s.add(dev)
            if dev.active:
                self.devices[r.id] = dev.id
                if r.id not in self.rooms:
                    self.rooms[r.id] = RoomSim(r.id, r.type, r.volume_m3,
                                               co2=self.rng.uniform(500, 750), pm25=self.rng.uniform(6, 14),
                                               extra={"vacant": r.code in VACANT_ROOMS})
            else:
                self.devices.pop(r.id, None)
                self.rooms.pop(r.id, None)
        s.commit()

    # ---------- fizik ----------
    def _occupancy(self, sim: RoomSim) -> int:
        h = self.sim_time.hour + self.sim_time.minute / 60
        mx = MAX_OCC.get(sim.rtype, 4)
        if sim.rtype == RoomType.waiting:
            day = max(0.0, math.sin(math.pi * (h - 7) / 13)) if 7 <= h <= 20 else 0.03
            target = mx * day
        elif sim.rtype == RoomType.corridor:
            target = mx * (0.6 if 8 <= h <= 19 else 0.1)
        elif sim.rtype == RoomType.icu:
            target = 1 + (1.5 if 9 <= h <= 18 else 0.3)  # hasta + personel
        else:  # hasta odası: ziyaret saatleri
            visit = 1.5 if (13 <= h <= 15 or 18 <= h <= 20) else 0.2
            target = 0 if sim.extra.get("vacant") else 1 + visit
        # yumuşak değişim + gürültü
        if sim.extra.get("vacant"):
            return 0
        new = sim.occ + (target - sim.occ) * 0.3 + self.rng.gauss(0, 0.35)
        return max(0, min(mx, round(new)))

    def _step_room(self, sim: RoomSim, dt: float) -> dict[str, float]:
        sim.occ = self._occupancy(sim)
        ach = 0.4 + (sim.fan / 100.0) * 6.0                    # hava değişimi / saat
        gen_ppm_s = sim.occ * CO2_GEN / 1000.0 / sim.volume * 1e6  # ppm/s
        # dC/dt = üretim - havalandırma*(C - Cdış); kararlı çözüm adım adım
        k = ach / 3600.0
        c_eq = CO2_OUT + gen_ppm_s / k
        sim.co2 = c_eq + (sim.co2 - c_eq) * math.exp(-k * dt) + self.rng.gauss(0, 4)
        # PM2.5: dış hava sızması + insan kaynaklı + temizleyici giderimi
        pm_out = 18 + 8 * math.sin(self.sim_time.hour / 24 * 2 * math.pi)
        k_pm = (0.3 + ach * 0.3) / 3600 + (sim.purifier / 100.0) * 7.0 / 3600
        src = sim.occ * 0.004 * (45 / sim.volume)
        if self.rng.random() < 0.002 * dt / 60:  # ara sıra olay (temizlik, kapı açılması)
            sim.pm25 += self.rng.uniform(15, 35)
        p_eq = (0.3 + ach * 0.3) / 3600 * pm_out / k_pm + src / k_pm
        sim.pm25 = max(1.0, p_eq + (sim.pm25 - p_eq) * math.exp(-k_pm * dt) + self.rng.gauss(0, 0.3))
        sim.temp += (22.5 + sim.occ * 0.15 - sim.temp) * 0.05 + self.rng.gauss(0, 0.03)
        sim.hum += (45 + sim.occ * 0.4 - sim.hum) * 0.05 + self.rng.gauss(0, 0.1)
        power = 15 + sim.fan * 0.6 + sim.purifier * 0.45 + sim.light * 0.35
        return {"co2_ppm": round(sim.co2, 1), "pm25": round(sim.pm25, 1), "occupancy": sim.occ,
                "temp_c": round(sim.temp, 2), "humidity": round(sim.hum, 1), "fan_pct": sim.fan,
                "purifier_pct": sim.purifier, "light_pct": sim.light, "power_w": round(power, 1)}

    def _step_energy(self, dt: float, room_power_w: float) -> dict[str, float]:
        h = self.sim_time.hour + self.sim_time.minute / 60
        self.cloud = max(0.25, min(1.0, self.cloud + self.rng.gauss(0, 0.03)))
        sun = max(0.0, math.sin(math.pi * (h - 7) / 10)) if 7 <= h <= 17 else 0.0
        pv = self.pv_peak_kw * sun * self.cloud
        load = 1.5 + room_power_w / 1000.0  # sabit yükler + odalar
        net = pv - load                       # + fazla, - açık
        hours = dt / 3600
        if net >= 0:
            charge = min(net * hours * 0.95, self.capacity_kwh - self.battery_kwh)
            self.battery_kwh += charge
            grid = -(net - charge / hours / 0.95) if hours else 0
        else:
            need = -net * hours
            discharge = min(need, max(0.0, self.battery_kwh - 0.15 * self.capacity_kwh))
            self.battery_kwh -= discharge
            grid = (need - discharge) / hours if hours else 0
        return {"pv_kw": round(pv, 3), "load_kw": round(load, 3),
                "battery_pct": round(100 * self.battery_kwh / self.capacity_kwh, 1),
                "grid_kw": round(grid, 3), "sim_hour": round(h, 2)}

    # ---------- komutlar ----------
    def _apply_commands(self, s: Session) -> None:
        dev_to_room = {v: k for k, v in self.devices.items()}
        if not dev_to_room:
            return
        cmds = s.exec(select(Command).where(Command.status == CommandStatus.pending,
                                            Command.device_id.in_(list(dev_to_room)))).all()
        for c in sorted(cmds, key=lambda c: c.created_at):
            sim = self.rooms.get(dev_to_room[c.device_id])
            if sim is None:
                continue
            setattr(sim, c.actuator, float(c.value))
            core.ack_command(s, c, CommandStatus.done)

    # ---------- döngü ----------
    def tick(self, s: Session) -> None:
        dt = settings.simulator_interval_s * settings.simulator_speed
        self._ensure_devices(s)
        self._apply_commands(s)
        self.sim_time += timedelta(seconds=dt)
        total_power = 0.0
        for room_id, sim in list(self.rooms.items()):
            m = self._step_room(sim, dt)
            total_power += m["power_w"]
            core.ingest(s, m, room_id, device_id=self.devices[room_id])
        # simüle olmayan (gerçek) odaların gücü de toplam yüke girsin
        core.ingest(s, self._step_energy(dt, total_power + 120 * (8 - len(self.rooms))), None,
                    device_id="SIM-SITE")

    def _tick_once(self) -> None:
        with Session(get_engine()) as s:
            self.tick(s)

    async def run(self) -> None:
        self.log.info("Simülatör başladı (%.0fx hız)", settings.simulator_speed)
        while True:
            await asyncio.to_thread(self._tick_once)
            await asyncio.sleep(settings.simulator_interval_s)
