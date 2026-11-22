"""Uygulama içi olay yolu.

Çekirdek (ölçüm kaydı, komut, karar) sadece olay yayınlar; MQTT, simülatör, ileride WebSocket
gibi eklentiler bu olaylara abone olur. Bir eklenti hata verirse çekirdek etkilenmez.
Olaylar: reading, command.created, command.updated, decision
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

log = logging.getLogger("hastane.bus")
_subs: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)


def subscribe(event: str, handler: Callable[[dict[str, Any]], None]) -> None:
    _subs[event].append(handler)


def unsubscribe_all() -> None:
    _subs.clear()


def publish(event: str, payload: dict[str, Any]) -> None:
    for h in list(_subs.get(event, [])):
        try:
            h(payload)
        except Exception:  # eklenti hatası çekirdeği durdurmasın
            log.exception("Olay işleyicisi hata verdi: %s", event)
