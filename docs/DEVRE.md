# Oda düğümü: devre bağlantı şeması

Bir fiziksel hastane odasını temsil eden ESP32 düğümünün bağlantıları. Düğüm, `docs/API.md`'deki cihaz akışıyla sunucuya bağlanır: `co2_ppm, pm25, occupancy, temp_c, humidity` (ve isteğe bağlı `lux`) gönderir, `fan`, `purifier`, `light` komutlarını uygular.

![Devre bağlantı şeması](img/devre-semasi.svg)

## Pin atamaları

| Parça | Parça pini | ESP32 pini | Not |
|---|---|---|---|
| SCD40/41 (CO2, sıcaklık, nem) | VCC | 3V3 | |
| | GND | GND | |
| | SDA | GPIO21 | I2C, adres 0x62 |
| | SCL | GPIO22 | |
| BH1750 (ışık, isteğe bağlı) | VCC | 3V3 | |
| | GND | GND | |
| | SDA / SCL | GPIO21 / GPIO22 | SCD40 ile aynı I2C hattı; ADDR → GND (adres 0x23) |
| PMS5003 (PM2.5) | 1 VCC | 5V hattı | Fanı 5V ister |
| | 2 GND | GND | |
| | 3 SET | GPIO4 (isteğe bağlı) | HIGH = çalış, LOW = uyku; boş bırakılırsa çalışır |
| | 4 RX | GPIO17 (TX2) | Lojik 3.3V, doğrudan bağlanır |
| | 5 TX | GPIO16 (RX2) | 9600 baud |
| | 6 RESET | boş | İçeride pull-up var |
| HC-SR501 PIR (doluluk) | VCC | 5V hattı | |
| | OUT | GPIO27 | Çıkış 3.3V, doğrudan bağlanır |
| | GND | GND | |
| 2 kanallı röle | VCC | 3V3 (JD-VCC jumper'ı çıkarılınca) | Bkz. uyarı 3 |
| | JD-VCC | 5V hattı | Röle bobinleri |
| | IN1 | GPIO26 | Işık (`light`) |
| | IN2 | GPIO25 | Hava temizleyici (`purifier`) |
| | GND | GND | |
| MOSFET modülü (fan) | SIG | GPIO19 | PWM, 25 kHz |
| | GND (sinyal) | GND | |
| | VIN / GND (güç) | Fan beslemesi (5V veya 12V) | |
| | V+ / V- | Fan + / Fan - | Fan uçlarına ters yönde 1N5819 (veya 1N4007) diyot |
| Durum LED'i | | GPIO2 | Kart üstündeki LED: Wi-Fi/sunucu bağlantısı |

Komut eşlemesi: `fan` değeri (0-100) doğrudan PWM görev oranı olur. `purifier` ve `light` rölede açık/kapalıdır: değer > 0 ise açık. Cihaz yeni değeri sonraki telemetride `fan_pct`, `purifier_pct`, `light_pct` olarak geri bildirir.

## Güç dağıtımı

- **5V fan ile:** 5V 2A adaptör → breadboard'un 5V hattı. Bu hattan ESP32 VIN, PMS5003, PIR, röle JD-VCC ve MOSFET üzerinden fan beslenir.
- **12V fan ile:** 12V adaptör → fan (MOSFET üzerinden) ve LM2596 girişi. LM2596 çıkışı, ESP32'ye bağlamadan **önce** multimetreyle 5.0V'a ayarlanır ve 5V hattını besler.
- ESP32'nin 3V3 pini (kart üstü regülatör) yalnızca SCD40, BH1750 ve röle modülünün lojik VCC'sini besler.
- **Tüm GND'ler ortaktır:** adaptör, ESP32, sensörler, röle, MOSFET modülü ve fan.
- Yaklaşık tüketim: ESP32 Wi-Fi tepe ~250 mA, PMS5003 ~100 mA, 2 röle ~150 mA, PIR ve SCD40 birkaç mA (SCD40 ölçüm anında kısa tepe), küçük 5V fan ~200 mA. Toplam 1 A'nın altında, 2A adaptör yeterli.
- PMS5003'ün fanı ve ESP32'nin Wi-Fi tepeleri için 5V hattına ESP32 yakınında 470 µF elektrolitik + 100 nF seramik kondansatör konur.

## Uyarılar

1. **3.3V lojik:** ESP32 pinleri 5V'a dayanıklı değildir. Bu tasarımda ESP32'ye giren tüm sinyaller (PMS5003 TX, PIR OUT, I2C) 3.3V seviyesindedir, seviye dönüştürücü gerekmez. I2C modüllerini 5V'tan beslemeyin, yoksa pull-up'lar SDA/SCL'yi 5V'a çeker.
2. **MOSFET seçimi:** IRF520 lojik seviyeli değildir, 3.3V kapı geriliminde tam iletime geçmez ve ısınır. Lojik seviyeli bir modül alın: **IRLZ44N** ya da **D4184 / AOD4184** modülü. Elinizde yalnız IRF520 varsa fan zayıf döner, araya bir transistör sürücüsü gerekir.
3. **Röle tetik seviyesi:** Yaygın 2 kanallı röle modülleri **aktif-LOW**'dur (IN = LOW → röle çeker). VCC 5V'a bağlıyken ESP32'nin 3.3V HIGH'ı bazı modüllerde röleyi tam bırakmaz. Çözüm: JD-VCC jumper'ını çıkarın, **VCC → 3V3**, **JD-VCC → 5V** bağlayın. Yazılımda açılışta önce `digitalWrite(pin, HIGH)` sonra `pinMode(pin, OUTPUT)` yapın ki röleler açılışta tıklamasın.
4. **Şebeke gerilimi yok:** Prototipte röleler yalnızca düşük gerilim (5V/12V) yükleri anahtarlar: LED şerit ve 5V/USB küçük hava temizleyici (ya da filtreli ikinci bir fan). 220V cihaz bağlamayın. Röle kontağı: COM ← yük beslemesi (+), NO → yükün + ucu, yükün - ucu → GND.
5. **Yazılım yüklerken:** USB takılıyken adaptör de bağlı olabilir (kartta koruma diyotu var), ama LM2596 çıkışını ayarlamadan asla bağlamayın.
6. **Kaçınılan pinler:** GPIO0, 2, 5, 12, 15 açılış (strapping) pinleridir; GPIO6-11 flaş belleğe aittir; GPIO34-39 yalnız giriştir. Röle ve PWM bu yüzden 25, 26, 19'a verildi.
7. **Sensör yerleşimi:** PIR'ı fan ve ısı kaynaklarından uzak, kapıya bakacak şekilde koyun. SCD40'ı fanın doğrudan hava akışından ve ESP32'nin ısısından birkaç cm uzakta tutun. PMS5003'ün hava giriş/çıkış delikleri kapanmamalı.
8. **PIR ayarı:** Jumper "H" (tekrar tetiklemeli), süre potu en düşük (~3 sn), açılıştan sonra ~60 sn ısınma beklenir. Doluluk, yazılımda son N dakikadaki hareketten türetilir (PIR kişi sayısı vermez, `occupancy` 0/1 olarak gönderilir).

## Kurulum sırası (önerilen)

1. ESP32'yi tek başına USB ile çalıştırıp Wi-Fi ve `POST /devices/me/telemetry` ile sahte değer gönderin.
2. I2C: SCD40 (+ BH1750) bağlayın, I2C taramasında 0x62 ve 0x23 görünmeli.
3. PMS5003'ü 5V hattı ve UART2'ye bağlayın.
4. PIR'ı ekleyin.
5. Adaptörü ve ortak GND'yi kurun; röle ve MOSFET + fanı en son ekleyin, önce yük olmadan tetik seviyelerini deneyin.
