# API sözleşmesi (v1)

Tüm uçlar `/api/v1` altındadır. Canlı ve denenebilir belge: sunucu çalışırken `/docs`.

**Kural:** v1 içinde alanlar sadece **eklenir**; var olan alan silinmez ya da yeniden adlandırılmaz. Kırıcı bir değişiklik gerekirse `/api/v2` açılır, v1 çalışmaya devam eder. Böylece ESP32 yazılımı ve agent'lar sunucu güncellendiğinde bozulmaz.

## Kimlik doğrulama

| Kim | Nasıl | Nereden alınır |
|---|---|---|
| Kullanıcı (panel) | `Authorization: Bearer <jwt>` | `POST /auth/login` |
| Cihaz (ESP32) | `X-Device-Key: dk_...` | Yönetici cihazı kaydedince bir kez gösterilir |
| Agent | `X-Agent-Key: ak_...` | Yönetici agent'ı kaydedince bir kez gösterilir |

Açık kayıt yoktur. Kullanıcı, cihaz ve agent'ları yönetici oluşturur; **tüm kimlikler sunucuda üretilir** (`usr_`, `room_`, `dev_`, `agt_`, `cmd_`, `dec_` önekli). Anahtarların sadece SHA-256 özeti saklanır.

Roller: `admin` (her şey), `operator` (izler + elle komut), `viewer` (sadece izler).

## ESP32 (cihaz) akışı

```
POST /devices/me/telemetry      {"metrics": {"co2_ppm": 812, "pm25": 9.4, "occupancy": 2}}
GET  /devices/me/commands       -> [{"id": "cmd_..", "actuator": "fan", "value": 70, ...}]
POST /devices/me/commands/{id}/ack   {"status": "done"}    (veya "failed")
```

- Metrik adları serbesttir; bilinenler: `co2_ppm, pm25, occupancy, temp_c, humidity, fan_pct, purifier_pct, light_pct, power_w`. Yeni sensör eklemek sunucu değişikliği gerektirmez.
- Cihaz eyleyicinin yeni değerini bir sonraki telemetride `fan_pct` vb. olarak bildirmelidir.
- Önerilen döngü: 5 sn'de bir telemetri + komut çekme.
- MQTT tercih edilirse (`HASTANE_MQTT=1`): `hastane/<device_id>/telemetry`, `.../commands`, `.../ack` (bkz. `backend/app/adapters/mqtt_bridge.py`).
- Bir odaya gerçek cihaz atanınca simülatör o odayı otomatik bırakır.

## Agent akışı

```
GET  /agents/me/state       -> {rooms: [...], energy: {...}, decisions: [...], ...}
POST /agents/me/decisions   {"summary": "YB1 CO2 yüksek", "room_id": "room_..", "level": "warning",
                             "actions": [{"room_id": "room_..", "actuator": "fan", "value": 80}]}
```

- `actuator`: `fan` (taze hava), `purifier` (hava temizleyici), `light`. `value`: 0-100.
- Her eylem komuta dönüşür ve odadaki uygun cihaza gider. Aynı eyleyiciye bekleyen eski komut varsa yenisi geçerli olur.
- Kararlar paneldeki karar akışında agent adıyla görünür.
- Örnek istemci: `examples/agent_ornek.py`. Yerleşik eşik tabanlı agent'lar `HASTANE_RULE_AGENT=0` ile kapatılabilir (değerlendirmede temel yöntem olarak kullanılır).

## Panel uçları (kullanıcı)

| Uç | Açıklama |
|---|---|
| `GET /overview` | Ana ekran: odalar + enerji + son kararlar |
| `GET /rooms`, `GET /rooms/{id}` | Oda durumu (`status`: ok, warning, critical, offline) |
| `GET /rooms/{id}/series?metric=co2_ppm&minutes=60` | Zaman serisi |
| `POST /rooms/{id}/commands` | Elle müdahale (operatör) |
| `GET /decisions` | Karar geçmişi |
| `GET/POST/PATCH /users` | Kullanıcı yönetimi (yönetici) |
| `GET/POST/PATCH /devices`, `POST /devices/{id}/rotate-key` | Cihaz yönetimi |
| `GET/POST/PATCH /agents` | Agent yönetimi |
| `GET /audit` | İşlem kaydı (yönetici) |

## Oda eşikleri (varsayılan)

| Oda | CO2 uyarı / kritik (ppm) | PM2.5 uyarı / kritik (µg/m³) |
|---|---|---|
| Yoğun bakım | 800 / 1000 | 12 / 25 |
| Hasta odası | 1000 / 1400 | 15 / 35 |
| Bekleme | 1000 / 1500 | 25 / 50 |
| Koridor | 1100 / 1600 | 25 / 50 |

Oda bazında `thresholds` alanıyla değiştirilebilir.
