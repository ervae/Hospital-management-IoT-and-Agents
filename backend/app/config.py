"""Uygulama ayarları. Tüm ayarlar ortam değişkeninden (HASTANE_ önekiyle) okunur."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("HASTANE_DATA_DIR", BASE_DIR / "data"))


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.lower() in ("1", "true", "yes", "evet")


def _secret() -> str:
    """JWT anahtarı: ortamdan, yoksa diskte kalıcı olarak üretilir (yeniden başlatınca oturumlar düşmesin)."""
    env = os.getenv("HASTANE_SECRET_KEY")
    if env:
        return env
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = DATA_DIR / ".secret_key"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(48))
        f.chmod(0o600)
    return f.read_text().strip()


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv(
        "HASTANE_DATABASE_URL", f"sqlite:///{DATA_DIR / 'hastane.db'}"))
    secret_key: str = field(default_factory=_secret)
    token_minutes: int = int(os.getenv("HASTANE_TOKEN_MINUTES", "720"))
    # İlk çalıştırmada oluşturulacak yönetici hesabı
    admin_username: str = os.getenv("HASTANE_ADMIN_USER", "admin")
    admin_password: str | None = os.getenv("HASTANE_ADMIN_PASSWORD")
    # Adaptörler: hangi veri kaynakları/agent'lar açık
    simulator_enabled: bool = _bool("HASTANE_SIMULATOR", True)
    simulator_interval_s: float = float(os.getenv("HASTANE_SIM_INTERVAL", "3"))
    simulator_speed: float = float(os.getenv("HASTANE_SIM_SPEED", "20"))  # 1 gerçek sn = 20 sim sn
    rule_agent_enabled: bool = _bool("HASTANE_RULE_AGENT", True)
    mqtt_enabled: bool = _bool("HASTANE_MQTT", False)
    mqtt_host: str = os.getenv("HASTANE_MQTT_HOST", "localhost")
    mqtt_port: int = int(os.getenv("HASTANE_MQTT_PORT", "1883"))
    reading_retention_days: int = int(os.getenv("HASTANE_RETENTION_DAYS", "30"))


settings = Settings()
