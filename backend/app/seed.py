"""İlk kurulum: yönetici hesabı ve 4 servisli örnek hastane (her serviste 6 tek yataklı oda)."""
from __future__ import annotations

import logging
import secrets

from sqlmodel import Session, select

from .config import DATA_DIR, settings
from .models import DEFAULT_PRIORITY, Role, Room, RoomType, User
from .security import hash_password

log = logging.getLogger("hastane.seed")

# (servis, kod öneki, kat)
WARDS = [("Dahiliye", "DH", 3), ("Genel Cerrahi", "GC", 4), ("Kardiyoloji", "KR", 5), ("Nöroloji", "NR", 6)]
ROOMS_PER_WARD = 6


def seed(s: Session) -> None:
    if not s.exec(select(User).where(User.role == Role.admin)).first():
        pw = settings.admin_password or secrets.token_urlsafe(10)
        s.add(User(username=settings.admin_username, full_name="Sistem Yöneticisi", role=Role.admin,
                   password_hash=hash_password(pw)))
        if not settings.admin_password:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            f = DATA_DIR / "ilk-yonetici-parolasi.txt"
            f.write_text(f"kullanici: {settings.admin_username}\nparola: {pw}\n")
            f.chmod(0o600)
            log.warning("Yönetici hesabı oluşturuldu: %s / %s (ilk girişte değiştirin)", settings.admin_username, pw)
    if not s.exec(select(Room)).first():
        for ward, prefix, floor in WARDS:
            for i in range(1, ROOMS_PER_WARD + 1):
                iso = i == ROOMS_PER_WARD  # her servisin son odası izolasyon odası
                rtype = RoomType.isolation if iso else RoomType.patient
                code = f"{prefix}-{floor}0{i}"
                s.add(Room(code=code, name=f"{ward} {floor}0{i}", ward=ward, type=rtype,
                           priority=DEFAULT_PRIORITY[rtype], floor=floor, isolation=iso,
                           # tek kişilik odalar; 2. ve 3. oda kadın/erkek ayrımlı örnek
                           sex={2: "F", 3: "M"}.get(i, "any"), layout={"order": i}))
    s.commit()
