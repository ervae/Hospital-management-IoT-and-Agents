# IoT → ajan arayüzü (ortak sözleşme)

Erva'nın IoT katmanı ile Cengizhan'ın ajan katmanı bu biçimle konuşur. Değişiklik ikisinin onayıyla yapılır.

## Oda durumları

| Kod | Durum | Sensör işareti | Ajanlar için anlamı |
|---|---|---|---|
| `occupied` | DOLU | Yük hücresi yatakta ağırlık algılıyor | Yatak kullanılamaz |
| `vacated` | BOŞALDI – TEMİZLİK BEKLİYOR | Ağırlık kalktı, kapı açılıp kapandı, odada hareket yok | Yakında hazır olacak; temizlik çağrılabilir |
| `cleaning` | TEMİZLENİYOR | Yatak boş, odada sürekli hareket | Tahmini 20–40 dk sonra hazır |
| `ready` | HAZIR | Hareket bitti, yatak boş, belirli süre değişiklik yok | Yeni hastaya atanabilir |
| `unknown` | BİLİNMİYOR | Sensör verisi gelmiyor (5 dk) | Son bilinen durumu ve HBYS bilgisini kullan |

## Oda durumu mesajı

`GET /api/v1/agents/me/state` içindeki her oda:

```json
{
  "id": "room_3f9c...", "code": "DH-301", "ward": "Dahiliye", "sex": "any", "isolation": false,
  "state": "ready", "confidence": 0.85, "state_source": "inference", "state_since": "2026-12-01T10:42:00Z",
  "his_state": "vacated", "his_updated_at": "2026-12-01T09:55:00Z",
  "hidden_free": true, "reserved_for": null, "online": true,
  "metrics": {"bed_load_kg": 0.4, "motion": 0, "door_events": 0, "temp_c": 22.6, "humidity": 45}
}
```

- `confidence`: 0–1 arası güven puanı. Kural motorunda sabit değerler; ML sınıflandırıcıda olasılık.
- `state_source`: `inference` (sunucudaki kural motoru), `external` (dış sınıflandırıcı), `manual` (operatör).
- Deneylerde "elle güncellenen HBYS" koşulu `his_state`, "gerçek zamanlı IoT" koşulu `state` alanını kullanır.

## Yatış talebi ve öneri

Bkz. [API.md](API.md) "Ajan akışı". Öneri en fazla 3 aday içerir; sunucu kısıtları denetler, insan onaylar.
