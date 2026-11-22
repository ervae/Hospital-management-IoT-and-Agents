"""Örnek dış ajan (Cengizhan için başlangıç noktası).

Çalıştırma:
  1) Panelde Yönetim > Ajanlar'dan ajan kaydedin, anahtarı kopyalayın.
  2) HASTANE_URL=http://localhost:8000 AGENT_KEY=ak_... python examples/agent_ornek.py
Sadece standart kütüphane kullanır. Kendi mantığınızı (LLM, LangGraph ...) `oner` içine yazın.
Sunucu her adayı kısıt katmanından geçirir; son karar paneldeki kullanıcıdadır.
"""
import json
import os
import time
import urllib.error
import urllib.request

URL = os.getenv("HASTANE_URL", "http://localhost:8000") + "/api/v1"
KEY = os.environ["AGENT_KEY"]


def call(method, path, body=None):
    req = urllib.request.Request(URL + path, method=method, headers={"X-Agent-Key": KEY, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or "null")


def oner(talep, odalar):
    """Bir talep için en fazla 3 aday oda döndürür. Burası ajanın beyni."""
    uygun = [o for o in odalar if o["state"] == "ready" and not o["reserved_for"] and o["ward"] == talep["ward"]
             and (not talep["isolation"] or o["isolation"]) and o["sex"] in ("any", talep["sex"])]
    # Yakında hazır olacak odalar da bilgi olarak kullanılabilir (vacated / cleaning)
    return [{"room_id": o["id"], "score": 1.0, "reason": f"{o['code']} hazır"} for o in uygun[:3]]


if __name__ == "__main__":
    onerilen = set()
    while True:
        durum = call("GET", "/agents/me/state")
        bekleyen = {r["request_id"] for r in durum["recommendations"] if r["status"] == "pending"}
        for talep in durum["requests"]:
            if talep["id"] in bekleyen or talep["id"] in onerilen:
                continue
            adaylar = oner(talep, durum["rooms"])
            if not adaylar:
                continue
            try:
                r = call("POST", "/agents/me/recommendations",
                         {"request_id": talep["id"], "candidates": adaylar, "rationale": "Örnek ajan: aynı serviste hazır oda"})
                print(talep["patient_ref"], "->", [c["room_code"] for c in r["candidates"]], r["status"])
                onerilen.add(talep["id"])
            except urllib.error.HTTPError as e:
                print("Hata:", e.read().decode())
        time.sleep(10)
