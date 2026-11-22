"""Temel (kural tabanlı) yatak atama ajanı.

İki işi var:
1) Cengizhan'ın LLM ajanları bağlanana kadar sistem canlı çalışsın, öneriler gelsin.
2) Deneyde karşılaştırılacak "kural tabanlı yöntem" olsun: talep için uygun ilk hazır yatak.

Dış ajanlarla aynı yolu kullanır (flow.submit_recommendation): öneri kısıt katmanından geçer,
panelde insan onayına sunulur. HASTANE_RULE_AGENT=0 ile kapatılır.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine
from ..models import Agent, BedState, Recommendation, RecStatus, Room, RoomStatus, utcnow
from ..services import core, flow
from .base import Adapter

NAME, DESC = "Temel atama (kural tabanlı)", "Talep için branşı, cinsiyeti ve izolasyonu uyan, en uzun süredir hazır bekleyen yatakları önerir."
RETRY_AFTER = timedelta(seconds=30)   # reddedilen/eskiyen öneriden sonra yeniden dene


class RuleAgents(Adapter):
    name = "rule_agent"

    def _agent(self, s: Session) -> Agent:
        ag = s.exec(select(Agent).where(Agent.builtin == True, Agent.agent_type == "baseline")).first()  # noqa: E712
        if ag is None:
            ag = Agent(name=NAME, agent_type="baseline", description=DESC, builtin=True)
            s.add(ag)
            s.commit()
            s.refresh(ag)
        ag.last_seen = utcnow()
        s.add(ag)
        s.commit()
        return ag

    def tick(self, s: Session) -> None:
        ag = self._agent(s)
        if not ag.active:
            return
        flow.revalidate_pending(s)
        states = {r.id: r for r in core.room_states(s)}
        rooms = {r.id: r for r in s.exec(select(Room)).all()}
        statuses = {st.room_id: st for st in s.exec(select(RoomStatus)).all()}
        taken: set[str] = set()
        now = utcnow()
        scale = settings.simulator_speed if settings.simulator_enabled else 1.0
        for req in flow.open_requests(s):   # aciliyet sırasıyla
            recs = s.exec(select(Recommendation).where(Recommendation.request_id == req.id)).all()
            if any(r.status == RecStatus.pending for r in recs):
                taken |= {c["room_id"] for r in recs if r.status == RecStatus.pending for c in r.candidates[:1]}
                continue
            if recs and now - core._aware(max(r.created_at for r in recs)) < RETRY_AFTER:
                continue
            rejected = {r.chosen_room_id or c["room_id"] for r in recs if r.status == RecStatus.rejected
                        for c in r.candidates[:1]}
            ok = []
            for rid, st in states.items():
                if rid in taken or rid in rejected or st.state != BedState.ready:
                    continue
                if not flow.check_candidate(rooms[rid], statuses.get(rid), req, st.state):
                    ok.append(st)
            if not ok:
                continue
            ok.sort(key=lambda r: (r.ward != req.ward, core._aware(r.state_since) or now))
            cands = [{"room_id": r.id, "score": round(1.0 - i * 0.1, 2),
                      "reason": f"{r.ward} servisinde hazır" + (
                          f", {int((now - core._aware(r.state_since)).total_seconds() * scale // 60)} dk'dır boş"
                          if r.state_since else "")} for i, r in enumerate(ok[:3])]
            taken.add(cands[0]["room_id"])
            flow.submit_recommendation(
                s, ag, req.id, cands,
                f"{req.patient_ref or req.id} ({req.ward}, aciliyet {req.urgency}) için uygun ilk hazır yatak.",
                {"method": "rule_first_fit"})

    def _tick_once(self) -> None:
        with Session(get_engine()) as s:
            self.tick(s)

    async def run(self) -> None:
        self.log.info("Temel atama ajanı başladı")
        while True:
            await asyncio.to_thread(self._tick_once)
            await asyncio.sleep(max(2.0, settings.simulator_interval_s))
