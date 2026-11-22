"""İlk kurulum: yönetici hesabı ve 8 odalı örnek hastane katı."""
from __future__ import annotations

import logging
import secrets

from sqlmodel import Session, select

from .config import DATA_DIR, settings
from .models import DEFAULT_PRIORITY, Role, Room, RoomType, User
from .security import hash_password

log = logging.getLogger("hastane.seed")

# (kod, ad, tip, hacim m3, kat planı konumu x,y,w,h — 100x56'lık plan üzerinde)
FLOOR = [
    ("YB1", "Yoğun Bakım 1", RoomType.icu, 40, (0, 0, 24, 22)),
    ("YB2", "Yoğun Bakım 2", RoomType.icu, 40, (25, 0, 24, 22)),
    ("H301", "Hasta Odası 301", RoomType.patient, 45, (50, 0, 24, 22)),
    ("H302", "Hasta Odası 302", RoomType.patient, 45, (75, 0, 25, 22)),
    ("KOR", "Koridor", RoomType.corridor, 120, (0, 23, 100, 8)),
    ("H303", "Hasta Odası 303", RoomType.patient, 45, (0, 32, 24, 22)),
    ("H304", "Hasta Odası 304", RoomType.patient, 45, (25, 32, 24, 22)),
    ("BEK", "Bekleme Salonu", RoomType.waiting, 110, (50, 32, 50, 22)),
]


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
        for code, name, rtype, vol, (x, y, w, h) in FLOOR:
            s.add(Room(code=code, name=name, type=rtype, priority=DEFAULT_PRIORITY[rtype], floor=3,
                       volume_m3=vol, layout={"x": x, "y": y, "w": w, "h": h}))
    s.commit()
