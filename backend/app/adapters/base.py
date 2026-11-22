"""Adaptör arayüzü. Yeni bir veri kaynağı ya da agent bağlamak = yeni bir Adapter sınıfı yazmak.
Çekirdek koda dokunmaya gerek yoktur; adaptör main.py'deki listeye eklenir ve ayarla açılıp kapanır."""
from __future__ import annotations

import asyncio
import logging


class Adapter:
    name = "adapter"

    def __init__(self) -> None:
        self.log = logging.getLogger(f"hastane.{self.name}")
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._guarded_run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _guarded_run(self) -> None:
        # Adaptör çökerse 5 sn sonra yeniden dener; sunucu ayakta kalır.
        while True:
            try:
                await self.run()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.exception("%s hata verdi, 5 sn sonra yeniden başlıyor", self.name)
                await asyncio.sleep(5)

    async def run(self) -> None:  # alt sınıf doldurur
        raise NotImplementedError
