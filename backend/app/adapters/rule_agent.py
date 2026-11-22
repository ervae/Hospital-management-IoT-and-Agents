"""Temel (eşik tabanlı) agent'lar.

İki işi var:
1) Cengizhan'ın akıllı agent'ları bağlanana kadar sistem canlı çalışsın, karar akışı dolsun.
2) Değerlendirmede karşılaştırılacak "temel yöntem" olsun (sabit eşik kuralları).

Dış agent'larla aynı yolu kullanır (core.record_decision), yani karar -> komut -> cihaz zinciri
dış agent'lar için de aynen böyle işler. HASTANE_RULE_AGENT=0 ile kapatılır.
"""
from __future__ import annotations

import asyncio

from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine
from ..models import Agent, utcnow
from ..schemas import RoomState
from ..services import core
from .base import Adapter

AGENTS = {
    "room": ("Oda Agent'ı (temel)", "Eşik kurallarıyla fan, hava temizleyici ve ışığı ayarlar."),
    "energy": ("Enerji Agent'ı (temel)", "Güneş, batarya ve şebeke durumuna göre tasarruf modunu açar."),
    "coordinator": ("Koordinatör (temel)", "Enerji kısıtında oda önceliğine göre yükleri sınırlar."),
}


class RuleAgents(Adapter):
    name = "rule_agent"

    def __init__(self) -> None:
        super().__init__()
        self.saving = False

    def _agent(self, s: Session, kind: str) -> Agent:
        name, desc = AGENTS[kind]
        ag = s.exec(select(Agent).where(Agent.builtin == True, Agent.agent_type == kind)).first()  # noqa: E712
        if ag is None:
            ag = Agent(name=name, agent_type=kind, description=desc, builtin=True)
            s.add(ag)
            s.commit()
            s.refresh(ag)
        ag.last_seen = utcnow()
        s.add(ag)
        return ag

    @staticmethod
    def _targets(r: RoomState) -> tuple[dict[str, float], list[str]]:
        m, t = r.metrics, core.thresholds_for(r)  # RoomState Room alanlarını taşır
        co2, pm, occ = m.get("co2_ppm"), m.get("pm25"), m.get("occupancy", 0)
        out, why = {}, []
        if co2 is not None:
            if co2 >= t["co2_crit"]:
                out["fan"] = 100
                why.append(f"CO2 {co2:.0f} ppm kritik eşiği ({t['co2_crit']}) aştı")
            elif co2 >= t["co2_warn"]:
                out["fan"] = 70
                why.append(f"CO2 {co2:.0f} ppm uyarı eşiğini ({t['co2_warn']}) aştı")
            elif co2 < t["co2_warn"] - 250:
                out["fan"] = 25 if occ else 10
                why.append(f"CO2 {co2:.0f} ppm normal")
        if pm is not None:
            if pm >= t["pm25_crit"]:
                out["purifier"] = 100
                why.append(f"PM2.5 {pm:.0f} kritik")
            elif pm >= t["pm25_warn"]:
                out["purifier"] = 60
                why.append(f"PM2.5 {pm:.0f} yüksek")
            elif pm < t["pm25_warn"] * 0.6:
                out["purifier"] = 0
        corridor = r.type.value == "corridor"
        out["light"] = (50 if corridor else 80) if occ else (20 if corridor else 0)
        return out, why

    def tick(self, s: Session) -> None:
        room_agent, energy_agent, coord = (self._agent(s, k) for k in ("room", "energy", "coordinator"))
        s.commit()
        energy = core.site_energy(s)
        rooms = core.room_states(s)

        # --- Enerji agent'ı: tasarruf modu ---
        if energy.battery_pct is not None:
            deficit = (energy.pv_kw or 0) < (energy.load_kw or 0)
            want = energy.battery_pct < 30 and deficit
            if want != self.saving and (want or energy.battery_pct > 40 or not deficit):
                self.saving = want
                core.record_decision(
                    s, energy_agent,
                    (f"Batarya %{energy.battery_pct:.0f} ve güneş üretimi yetersiz: tasarruf modu açıldı"
                     if want else f"Batarya %{energy.battery_pct:.0f}: tasarruf modu kapatıldı"),
                    None, "warning" if want else "info", [], {"saving": want})

        # --- Oda agent'ı + koordinatör ---
        for r in rooms:  # öncelik sırasıyla gelir
            if r.status == "offline":
                continue
            targets, why = self._targets(r)
            capped = []
            if self.saving and r.priority >= 3 and r.status != "critical":
                for act, cap in (("fan", 50), ("purifier", 40)):
                    if targets.get(act, 0) > cap:
                        targets[act] = cap
                        capped.append(act)
            actions = []
            for act, val in targets.items():
                cur = r.metrics.get(f"{act}_pct")
                if cur is None or abs(cur - val) >= 5:
                    actions.append({"room_id": r.id, "actuator": act, "value": val})
            if not actions:
                continue
            names = {"fan": "taze hava fanı", "purifier": "hava temizleyici", "light": "ışık"}
            change = ", ".join(f"{names[a['actuator']]} %{a['value']:.0f}" for a in actions)
            only_light = all(a["actuator"] == "light" for a in actions)
            reason = "; ".join(why) if why and not only_light else (
                f"Doluluk {r.metrics.get('occupancy', 0):.0f} kişi")
            level = "critical" if r.status == "critical" else "warning" if r.status == "warning" else "info"
            if capped:
                core.record_decision(s, coord, f"{r.name}: enerji tasarrufu nedeniyle "
                                     f"{', '.join(names[c] for c in capped)} sınırlandı (öncelik {r.priority})",
                                     r.id, "info", [], {"capped": capped})
            core.record_decision(s, room_agent, f"{r.name}: {reason} → {change}", r.id, level, actions,
                                 {"metrics": r.metrics})

    def _tick_once(self) -> None:
        with Session(get_engine()) as s:
            self.tick(s)

    async def run(self) -> None:
        self.log.info("Temel kural agent'ları başladı")
        await asyncio.sleep(settings.simulator_interval_s * 1.5)
        while True:
            await asyncio.to_thread(self._tick_once)
            await asyncio.sleep(settings.simulator_interval_s * 2)
