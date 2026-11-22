"""Demo kolaylığı: bekleyen öneriyi belirli süre sonra ilk geçerli adayla otomatik onaylar.
Varsayılan kapalıdır (HASTANE_AUTO_APPROVE_S=0). Gerçek kullanımda son karar her zaman insandadır."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException
from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine
from ..models import Recommendation, RecStatus, utcnow
from ..services import core, flow
from .base import Adapter


class AutoApprove(Adapter):
    name = "auto_approve"

    def tick(self, s: Session) -> None:
        now = utcnow()
        flow.revalidate_pending(s)
        for rec in s.exec(select(Recommendation).where(Recommendation.status == RecStatus.pending)).all():
            if (now - core._aware(rec.created_at)).total_seconds() >= settings.auto_approve_s:
                try:
                    flow.approve(s, rec, None, "otomatik onay (demo)", "system:auto")
                except HTTPException:
                    rec.status = RecStatus.superseded  # artık geçerli değil; ajan yeniden önerir
                    s.add(rec)
                    s.commit()

    async def run(self) -> None:
        while True:
            await asyncio.to_thread(self._once)
            await asyncio.sleep(2)

    def _once(self) -> None:
        with Session(get_engine()) as s:
            self.tick(s)
