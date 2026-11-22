# Akıllı Hastane: IoT ve Agent'larla İç Hava Kalitesi ve Enerji Yönetimi

İstanbul Sağlık ve Teknoloji Üniversitesi, Bilgisayar Mühendisliği bitirme projesi.
Ekip: Erva Ergül (web, devre, kat simülasyonu), Cengizhan Karaman (agent'lar). Danışman: Dr. Nazlı Nakeeb Tokatlı.

![Panel](docs/img/panel.png)

## Ne yapar
- 8 odalı bir hastane katını (yoğun bakım > hasta odası > bekleme > koridor önceliği) izler.
- CO2 → taze hava fanı, PM2.5 → hava temizleyici, doluluk → aydınlatma, güneş paneli + batarya ile enerji yönetimi.
- Kararları agent'lar verir; panelde kat planı, enerji şeridi ve agent karar akışı canlı görünür.

## Çalıştırma
```bash
cd backend
pip install -r requirements.txt
HASTANE_ADMIN_PASSWORD='guclu-bir-parola' uvicorn app.main:app --port 8000
```
Tarayıcıda `http://localhost:8000` açın, `admin` ile girin. Parola verilmezse sunucu üretir ve `backend/data/ilk-yonetici-parolasi.txt` dosyasına yazar. API belgesi: `http://localhost:8000/docs`.

Testler: `cd backend && python -m pytest -q`

## Mimari
```
ESP32 ──HTTP/MQTT──┐                          ┌── Panel (frontend/)
Simülatör ─────────┼──► Çekirdek (services/core.py) ◄──┤
Dış agent'lar ─────┘     ölçüm · komut · karar    └── Yönetim
                         │
                    Olay yolu (services/bus.py) ──► adaptörler (MQTT, simülatör, ...)
```
- **Çekirdek** tek yerdir: veri nereden gelirse gelsin aynı kurallardan geçer.
- **Adaptörler** (`backend/app/adapters/`) birbirinden bağımsızdır; biri çökerse sunucu ayakta kalır ve adaptör yeniden başlar. Yeni entegrasyon = yeni adaptör + `main.py`'de bir satır.
- **Ölçümler** genel metrik/değer biçiminde: yeni sensör tablo değişikliği gerektirmez.
- **API sözleşmesi** sadece eklenerek büyür: [docs/API.md](docs/API.md).

## Ayarlar (ortam değişkenleri)
| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `HASTANE_DATABASE_URL` | `sqlite:///backend/data/hastane.db` | PostgreSQL de olur |
| `HASTANE_ADMIN_USER` / `HASTANE_ADMIN_PASSWORD` | `admin` / üretilir | İlk yönetici |
| `HASTANE_SIMULATOR` | `1` | Kat simülatörü |
| `HASTANE_SIM_SPEED` | `20` | Simülasyon hızı (20x: bir gün ~72 dk) |
| `HASTANE_RULE_AGENT` | `1` | Yerleşik eşik tabanlı agent'lar (temel yöntem) |
| `HASTANE_MQTT`, `HASTANE_MQTT_HOST`, `HASTANE_MQTT_PORT` | `0`, `localhost`, `1883` | MQTT köprüsü |
| `HASTANE_SECRET_KEY` | üretilir | Oturum imzalama anahtarı |
