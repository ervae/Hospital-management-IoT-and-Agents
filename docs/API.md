# API sözleşmesi (v1)

Tüm uçlar `/api/v1` altındadır. Canlı ve denenebilir belge: sunucu çalışırken `/docs`.

**Kural:** v1 içinde alanlar sadece **eklenir**; var olan alan silinmez ya da yeniden adlandırılmaz. Kırıcı bir değişiklik gerekirse `/api/v2` açılır. Böylece ESP32 yazılımı ve ajanlar sunucu güncellendiğinde bozulmaz.

IoT katmanı ile ajan katmanı arasındaki veri biçimi ve oda durumlarının tanımı: [interface.md](interface.md).

## Kimlik doğrulama

| Kim | Nasıl | Nereden alınır |
|---|---|---|
| Kullanıcı (panel) | `Authorization: Bearer <jwt>` | `POST /auth/login` |
| Cihaz (ESP32) | `X-Device-Key: dk_...` | Yönetici cihazı kaydedince bir kez gösterilir |
| Ajan | `X-Agent-Key: ak_...` | Yönetici ajanı kaydedince bir kez gösterilir |

Açık kayıt yoktur. Kullanıcı, cihaz ve ajanları yönetici oluşturur; **tüm kimlikler sunucuda üretilir** (`usr_`, `room_`, `dev_`, `agt_`, `req_`, `rec_`, `cmd_`, `dec_` önekli). Anahtarların sadece SHA-256 özeti saklanır.

Roller: `admin` (her şey), `operator` (izler, öneri onaylar/reddeder, talep açar, durumu düzeltir), `viewer` (sadece izler).

## ESP32 (oda düğümü) akışı

```
POST /devices/me/telemetry   {"metrics": {"bed_load_kg": 71.5, "motion": 0, "door_events": 1, "temp_c": 22.8, "humidity": 44}}
POST /devices/me/state       {"state": "ready", "confidence": 0.9}     (isteğe bağlı: durumu cihaz/ML hesaplıyorsa)
GET  /devices/me/commands    -> [{"id": "cmd_..", "actuator": "led", "value": 1, ...}]
POST /devices/me/commands/{id}/ack   {"status": "done"}
```

- Bilinen metrikler: `bed_load_kg` (yük hücresi), `bed_occupied` (0/1, FSR vb.), `motion` (ölçüm aralığındaki PIR tetik sayısı), `door_open` (0/1), `door_events` (aralıktaki kapı açılma sayısı), `temp_c`, `humidity`, `co2_ppm` (isteğe bağlı). Yeni metrik eklemek sunucu değişikliği gerektirmez.
- Sunucu oda durumunu bu ölçümlerden kendisi çıkarır (`backend/app/services/room_state.py`). Eşikler oda bazında `thresholds` ile değişir: `load_kg` (20), `motion_min` (2), `cleaning_after_s` (120), `quiet_s` (600).
- Odada dış sınıflandırıcı kullanılacaksa oda ayarına `{"inference": "external"}` yazılır ve sonuç `POST /devices/me/state` ile gönderilir.
- Önerilen döngü: 5 sn'de bir telemetri. MQTT tercih edilirse (`HASTANE_MQTT=1`): `hastane/<device_id>/telemetry`, `.../commands`, `.../ack`.
- Bir odaya gerçek cihaz atanınca simülatör o odayı otomatik bırakır.

## Ajan akışı

```
GET  /agents/me/state             -> {rooms: [...], requests: [...], recommendations: [...], kpis: {...}}
POST /agents/me/requests          {"patient_ref": "H-1042", "ward": "Dahiliye", "sex": "F", "urgency": 2, "isolation": false}
POST /agents/me/recommendations   {"request_id": "req_..", "rationale": "...",
                                   "candidates": [{"room_id": "room_..", "score": 0.9, "reason": "..."}]}   (en fazla 3 aday)
POST /agents/me/decisions         {"summary": "Acilde 3 hasta bekliyor", "level": "info"}   (serbest metin kayıt)
```

- Her odada iki durum vardır: `state` (IoT, gerçek zamanlı) ve `his_state` (HBYS'ye elle girilen). `hidden_free: true` = IoT'ye göre hazır ama HBYS'de hazır görünmüyor.
- **Kısıt katmanı (sunucu, deterministik):** yatak hazır olmalı, başka hastaya ayrılmamış olmalı, branş uymalı (`allow_overflow` yoksa), oda cinsiyeti uymalı, izolasyon gerekiyorsa izolasyon odası olmalı. İhlalli aday `violations` ile işaretlenir; tüm adaylar ihlalliyse öneri `blocked` olur. Kurallar onay anında yeniden denetlenir.
- Bir talep için yeni öneri gelirse eskisi `superseded` olur. Oda durumu değişip aday geçersizleşirse öneri kendiliğinden `superseded` olur; ajan yeniden önerir.
- Ajan yatağı hiçbir zaman doğrudan atamaz: son karar paneldeki kullanıcıdadır. Her adım `/audit` kaydına yazılır.
- Örnek istemci: `examples/agent_ornek.py`. Yerleşik kural tabanlı ajan `HASTANE_RULE_AGENT=0` ile kapatılır.

## Panel uçları (kullanıcı)

| Uç | Açıklama |
|---|---|
| `GET /overview` | Ana ekran: odalar + göstergeler + bekleyen talepler + öneriler |
| `GET /rooms?ward=Dahiliye`, `GET /rooms/{id}` | Oda durumu (`state`, `his_state`, `hidden_free`, `online` ...) |
| `POST /rooms/{id}/state` | Durumu elle düzelt (operatör) |
| `POST /rooms/{id}/his-state` | HBYS'ye elle giriş (operatör) |
| `GET /rooms/{id}/transitions?channel=iot\|his&hours=24` | Durum geçişleri (F1 / gecikme analizi için) |
| `GET/POST /rooms/{id}/labels` | Laboratuvar etiketleri (gerçek durum) |
| `GET /rooms/{id}/series?metric=bed_load_kg&minutes=60` | Zaman serisi |
| `GET/POST /requests`, `POST /requests/{id}/cancel` | Yatış talepleri |
| `GET /recommendations?status=pending` | Ajan önerileri |
| `POST /recommendations/{id}/approve` `{"room_id": "..", "note": ".."}` | Onay (oda verilmezse ilk geçerli aday) |
| `POST /recommendations/{id}/reject` `{"note": ".."}` | Ret |
| `GET/POST/PATCH /users`, `/devices`, `/agents` | Yönetim (yönetici) |
| `GET /audit` | İşlem kaydı (yönetici) |
