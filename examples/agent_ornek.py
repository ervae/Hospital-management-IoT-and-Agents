"""Örnek dış agent (Cengizhan için başlangıç noktası).

Çalıştırma:
  1) Panelde Yönetim > Agent'lar'dan agent kaydedin, anahtarı kopyalayın.
  2) HASTANE_URL=http://localhost:8000 AGENT_KEY=ak_... python examples/agent_ornek.py
Sadece standart kütüphane kullanır. Kendi mantığınızı `karar_ver` içine yazın.
"""
import json
import os
import time
import urllib.request

URL = os.getenv("HASTANE_URL", "http://localhost:8000") + "/api/v1"
KEY = os.environ["AGENT_KEY"]


def call(method, path, body=None):
    req = urllib.request.Request(URL + path, method=method, headers={"X-Agent-Key": KEY, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or "null")


def karar_ver(durum):
    """Durumu okuyup (özet, eylemler) listesi döndürür. Burası agent'ın beyni."""
    out = []
    for oda in durum["rooms"]:
        co2 = oda["metrics"].get("co2_ppm")
        if co2 and co2 > 900 and oda["metrics"].get("fan_pct", 0) < 60:
            out.append({"summary": f"{oda['name']}: CO2 {co2:.0f} ppm, fan artırılıyor", "room_id": oda["id"],
                        "level": "warning", "actions": [{"room_id": oda["id"], "actuator": "fan", "value": 60}]})
    return out


if __name__ == "__main__":
    while True:
        durum = call("GET", "/agents/me/state")
        for karar in karar_ver(durum):
            print(call("POST", "/agents/me/decisions", karar)["summary"])
        time.sleep(10)
