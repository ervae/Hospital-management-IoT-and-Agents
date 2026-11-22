"""Oda durumu çıkarımı (v1: kural tabanlı durum makinesi).

Girdi: odadan gelen sensör ölçümleri (yük hücresi, PIR, kapı). Çıktı: oda durumu + güven puanı.

    DOLU ──ağırlık kalktı──► BOŞALDI ──sürekli hareket──► TEMİZLENİYOR ──sessizlik──► HAZIR
     ▲                                                                                  │
     └────────────────────────────── ağırlık geldi ─────────────────────────────────────┘
    Veri gelmezse: BİLİNMİYOR

Kurallar tek bir saf fonksiyonda (`step`) durur; veritabanı bilmez. Böylece aynı fonksiyon
laboratuvar kayıtları üzerinde çevrimdışı da çalıştırılıp F1 ve gecikme ölçülebilir.
İleride ML sınıflandırıcı gelince oda ayarına {"inference": "external"} yazılır ve sınıflandırıcı
sonucunu POST /rooms/{id}/state ile gönderir; bu motor o odayı atlar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..models import BedState

DEFAULTS = {
    "load_kg": 20.0,         # bu ağırlığın üstü = yatakta hasta
    "motion_min": 2,         # bir ölçümde bu kadar PIR tetiği = "sürekli hareket"
    "cleaning_after_s": 120,  # boşaldıktan sonra bu süre boyunca hareket sürerse temizlik başladı
    "quiet_s": 600,          # temizlikten sonra bu kadar sessizlik = hazır
    "time_scale": 1.0,       # simülatör hızlandırılmış zaman kullanıyorsa süreler bölünür
}


@dataclass
class Result:
    state: BedState
    confidence: float
    ctx: dict


def params(room_thresholds: dict | None, device_caps: dict | None = None) -> dict:
    p = {**DEFAULTS, **(room_thresholds or {})}
    if device_caps and device_caps.get("time_scale"):
        p["time_scale"] = float(device_caps["time_scale"])
    return p


def _occupied(metrics: dict, p: dict) -> bool | None:
    if "bed_load_kg" in metrics:
        return metrics["bed_load_kg"] >= p["load_kg"]
    if "bed_occupied" in metrics:
        return metrics["bed_occupied"] >= 0.5
    return None


def step(state: BedState, ctx: dict, metrics: dict, ts: datetime, p: dict) -> Result:
    """Bir ölçüm geldiğinde yeni durumu hesaplar. ctx motorun hafızasıdır (zamanlar epoch saniye)."""
    ctx = dict(ctx or {})
    now = ts.timestamp()
    scale = max(float(p.get("time_scale") or 1.0), 1e-6)
    occ = _occupied(metrics, p)
    motion = float(metrics.get("motion", 0) or 0)
    door = float(metrics.get("door_open", 0) or 0) + float(metrics.get("door_events", 0) or 0)
    if motion > 0:
        ctx["last_motion"] = now
    if motion >= p["motion_min"]:
        ctx.setdefault("busy_since", now)
    elif "busy_since" in ctx and now - ctx.get("last_motion", now) > 60 / scale:
        ctx.pop("busy_since", None)
    if door > 0:
        ctx["last_door"] = now

    if occ is None:  # yatak sensörü yok: durumu koru, güveni düşür
        return Result(state, 0.4 if state != BedState.unknown else 0.0, ctx)

    if occ:
        ctx.pop("vacated_at", None)
        ctx.pop("cleaning_at", None)
        return Result(BedState.occupied, 0.95, ctx)

    # Yatak boş
    if state == BedState.occupied:
        ctx["vacated_at"] = now
        ctx.pop("busy_since", None)
        return Result(BedState.vacated, 0.85 if door else 0.7, ctx)

    if state == BedState.unknown:
        # İlk veri ya da arıza sonrası: hareket varsa temizleniyor, yoksa hazır varsay (düşük güven)
        if motion >= p["motion_min"]:
            return Result(BedState.cleaning, 0.5, ctx)
        return Result(BedState.ready, 0.5, ctx)

    if state == BedState.vacated:
        busy = ctx.get("busy_since")
        if busy is not None and now - busy >= p["cleaning_after_s"] / scale:
            ctx["cleaning_at"] = now
            return Result(BedState.cleaning, 0.8, ctx)
        return Result(BedState.vacated, 0.8, ctx)

    if state == BedState.cleaning:
        last = ctx.get("last_motion", ctx.get("cleaning_at", now))
        if now - last >= p["quiet_s"] / scale:
            ctx.pop("busy_since", None)
            return Result(BedState.ready, 0.85, ctx)
        return Result(BedState.cleaning, 0.8, ctx)

    # ready: hazır kalır. Uzun hareket olursa (ör. ikinci temizlik) yine hazır sayılır.
    return Result(BedState.ready, 0.9, ctx)
