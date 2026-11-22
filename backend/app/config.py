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
    simulator_speed: float = float(os.getenv("HASTANE_SIM_SPEED", "60"))  # 1 gerçek sn = 60 sim sn
    # Sanal sensör arızaları: veri kaybı oranı (0-1) ve hatalı okuma oranı (0-1)
    sim_drop: float = float(os.getenv("HASTANE_SIM_DROP", "0"))
    sim_noise: float = float(os.getenv("HASTANE_SIM_NOISE", "0"))
    # HBYS gecikmesi: yatak hazır olduktan kaç simülasyon dakikası sonra HBYS'ye girilir
    his_delay_min: float = float(os.getenv("HASTANE_HIS_DELAY_MIN", "60"))
    # Günlük acil servis yatış talebi (oda başına); 0.2 x 24 oda ≈ günde 5 talep
    sim_ed_rate: float = float(os.getenv("HASTANE_SIM_ED_RATE", "0.2"))
    rule_agent_enabled: bool = _bool("HASTANE_RULE_AGENT", True)
    # Demo için: bekleyen öneri bu kadar (gerçek) saniye sonra otomatik onaylansın. 0 = kapalı
    auto_approve_s: float = float(os.getenv("HASTANE_AUTO_APPROVE_S", "0"))
    mqtt_enabled: bool = _bool("HASTANE_MQTT", False)
    mqtt_host: str = os.getenv("HASTANE_MQTT_HOST", "localhost")
    mqtt_port: int = int(os.getenv("HASTANE_MQTT_PORT", "1883"))
    reading_retention_days: int = int(os.getenv("HASTANE_RETENTION_DAYS", "30"))


settings = Settings()
