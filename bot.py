import logging
import asyncio
import sqlite3
import json
import requests
from datetime import datetime, date, timedelta
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ─────────────────────────────────────────────
# AYARLAR — sadece buraya yaz
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = "8686459551:AAFZP4JWH_tp1ggE7CHMlmuAruQu3ethNfA"
TELEGRAM_CHAT_ID = "529241059"
SKYSCANNER_KEY   = "uc643373167396223405725428773537"
TARAMA_SAATI     = 7   # Her sabah saat 07:00
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DB_FILE = "ucus_takip.db"

# ── ROTALAR ───────────────────────────────────
ROTALAR = [
    ("IST", "GYD"), ("IST", "BUS"), ("IST", "TBS"), ("IST", "ECN"),
    ("IST", "BEG"), ("IST", "SJJ"), ("IST", "SKP"), ("IST", "PRN"),
    ("IST", "ATH"), ("IST", "SKG"), ("IST", "BER"), ("IST", "MUC"),
    ("IST", "FRA"), ("IST", "DUS"), ("IST", "PAR"), ("IST", "FCO"),
    ("IST", "MIL"), ("IST", "VCE"), ("IST", "AMS"), ("IST", "VIE"),
    ("IST", "PRG"), ("IST", "BUD"), ("IST", "BCN"), ("IST", "MAD"),
    ("IST", "LON"), ("IST", "ZRH"), ("IST", "BRU"), ("IST", "CPH"),
    ("IST", "ARN"), ("IST", "WAW"), ("IST", "JED"), ("IST", "MED"),
    ("IST", "DXB"), ("IST", "AUH"), ("IST", "DOH"), ("IST", "BEY"),
    ("IST", "CAI"), ("IST", "SSH"), ("IST", "HRG"), ("IST", "BKK"),
    ("IST", "HKT"), ("IST", "DPS"), ("IST", "TYO"), ("IST", "SEL"),
    ("IST", "NYC"), ("IST", "MIA"), ("IST", "LAX"), ("IST", "KUL"),
    ("IST", "HKG"), ("IST", "MLE"), ("IST", "NBO"), ("IST", "LYS"),
    ("ADB", "LIS"), ("ESB", "MAD"), ("ESB", "BCN"), ("ADB", "SKP"),
    ("ESB", "VIE"), ("ESB", "LON"), ("ADB", "LON"),
]

EUR_RATES = {
    "EUR": 1.0, "USD": 0.92, "GBP": 1.17, "TRY": 0.028,
    "CAD": 0.68, "AUD": 0.60, "INR": 0.011, "JPY": 0.0063,
    "CHF": 1.04, "SEK": 0.088, "NOK": 0.086, "DKK": 0.134,
    "PLN": 0.23, "CZK": 0.041, "HUF": 0.0026, "RON": 0.20,
    "HKD": 0.12, "SGD": 0.69, "THB": 0.026, "AED": 0.25,
    "SAR": 0.25, "ZAR": 0.050, "BRL": 0.18, "MXN": 0.053,
    "TWD": 0.029, "KRW": 0.00067,
}

SKYSCANNER_URL = "https://partners.api.skyscanner.net/apiservices/v3/flights/indicative/search"


# ── VERİTABANI ────────────────────────────────

def db_baslat():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS fiyatlar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kalkis TEXT,
            varis TEXT,
            yil INTEGER,
            ay INTEGER,
            min_fiyat_eur REAL,
            min_fiyat_yerel REAL,
            para_birimi TEXT,
            kaynak TEXT,
            tarih TEXT,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bildirimler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kalkis TEXT,
            varis TEXT,
            yil INTEGER,
            ay INTEGER,
            eski_fiyat REAL,
            yeni_fiyat REAL,
            dusus_miktari REAL,
            dusus_yuzdesi REAL,
            bildirim_tarihi TEXT
        )
    """)
    conn.commit()
    conn.close()


def son_fiyat_al(kalkis, varis, yil, ay):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT min_fiyat_eur, kaynak FROM fiyatlar
        WHERE kalkis=? AND varis=? AND yil=? AND ay=?
        ORDER BY created_at DESC LIMIT 1
    """, (kalkis, varis, yil, ay))
    row = c.fetchone()
    conn.close()
    return row


def fiyat_kaydet(kalkis, varis, yil, ay, min_eur, min_yerel, para_birimi, kaynak):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO fiyatlar
        (kalkis, varis, yil, ay, min_fiyat_eur, min_fiyat_yerel, para_birimi, kaynak, tarih, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        kalkis, varis, yil, ay, min_eur, min_yerel, para_birimi, kaynak,
        f"{yil}-{ay:02d}", datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()


def bildirim_kaydet(kalkis, varis, yil, ay, eski, yeni):
    dusus = eski - yeni
    yuzde = (dusus / eski) * 100 if eski > 0 else 0
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO bildirimler
        (kalkis, varis, yil, ay, eski_fiyat, yeni_fiyat, dusus_miktari, dusus_yuzdesi, bildirim_tarihi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (kalkis, varis, yil, ay, eski, yeni, dusus, yuzde, datetime.now().isoformat()))
    conn.commit()
    conn.close()


# ── SKYSCANNER ────────────────────────────────

def skyscanner_link_olustur(kalkis, varis, yil, ay):
    """Skyscanner aylık takvim görünümü — o ayın tüm gidiş-dönüş fiyatlarını gösterir."""
    import calendar
    donus_ay = ay + 1 if ay < 12 else 1
    donus_yil = yil if ay < 12 else yil + 1
    oym = f"{yil}-{ay:02d}"      # outbound month
    iym = f"{donus_yil}-{donus_ay:02d}"  # inbound month (1 ay sonra)
    return (
        f"https://www.skyscanner.net/transport/flights/"
        f"{kalkis.lower()}/{varis.lower()}/"
        f"?adults=1&cabinclass=economy&rtn=1"
        f"&oym={oym}&iym={iym}"
    )


def skyscanner_aylik_ara(kalkis, varis, yil, ay):
    """Tek istekte tüm ayın en ucuz gidiş-dönüş fiyatını çeker."""
    try:
        payload = {
            "query": {
                "market": "TR",
                "locale": "tr-TR",
                "currency": "EUR",
                "queryLegs": [
                    {
                        "originPlace": {"queryPlace": {"iata": kalkis}},
                        "destinationPlace": {"queryPlace": {"iata": varis}},
                        "dateRange": {
                            "startDate": {"year": yil, "month": ay},
                            "endDate": {"year": yil, "month": ay},
                        },
                    },
                    {
                        "originPlace": {"queryPlace": {"iata": varis}},
                        "destinationPlace": {"queryPlace": {"iata": kalkis}},
                        "dateRange": {
                            "startDate": {"year": yil, "month": ay},
                            "endDate": {"year": yil, "month": ay},
                        },
                    },
                ],
                "dateTimeGroupingType": "DATE_TIME_GROUPING_TYPE_BY_DATE",
            }
        }
        headers = {"x-api-key": SKYSCANNER_KEY, "Content-Type": "application/json"}
        r = requests.post(SKYSCANNER_URL, json=payload, headers=headers, timeout=12)

        if r.status_code != 200:
            logger.warning(f"Skyscanner [{kalkis}-{varis} {yil}/{ay}] HTTP {r.status_code}")
            return None

        data = r.json()
        quotes = data.get("content", {}).get("results", {}).get("quotes", {})
        if not quotes:
            return None

        en_ucuz = None
        for q in quotes.values():
            try:
                amount = float(q["minPrice"]["amount"])
                if amount > 0:
                    if en_ucuz is None or amount < en_ucuz:
                        en_ucuz = amount
            except (KeyError, TypeError, ValueError):
                continue

        return en_ucuz

    except Exception as e:
        logger.error(f"Skyscanner hata [{kalkis}-{varis}]: {e}")
        return None


# ── GOOGLE FLIGHTS ────────────────────────────

def google_flights_ara(kalkis, varis, tarih_str):
    """Google Flights'tan belirli tarih için fiyat çeker."""
    try:
        from gf_search import search
        results = search(kalkis, varis, tarih_str)
        en_ucuz = None
        for r in results:
            fiyat_str = r.get("price", "")
            if not fiyat_str:
                continue
            parcalar = fiyat_str.split()
            if len(parcalar) == 2:
                try:
                    kod = parcalar[0].upper()
                    miktar = float(parcalar[1].replace(",", ""))
                    kur = EUR_RATES.get(kod, None)
                    if kur and miktar > 0:
                        eur = miktar * kur
                        if en_ucuz is None or eur < en_ucuz:
                            en_ucuz = eur
                except ValueError:
                    continue
        return en_ucuz
    except ImportError:
        pass
    except Exception as e:
        logger.error(f"Google Flights hata [{kalkis}-{varis}]: {e}")
    return None


# ── ANA TARAMA ────────────────────────────────

async def tum_rotalari_tara(bot: Bot):
    """Tüm rotaları tara, fiyat düşüşlerini bildir."""
    bugun = date.today()
    dusen_rotalar = []
    bulunan_rotalar = []
    bulunamayan_rotalar = []

    logger.info(f"Tarama başladı: {len(ROTALAR)} rota × 12 ay")

    for kalkis, varis in ROTALAR:
        rota_min_eur = None
        rota_min_ay = None

        for ay_offset in range(1, 13):  # Önümüzdeki 12 ay
            hedef = bugun + timedelta(days=30 * ay_offset)
            yil, ay = hedef.year, hedef.month

            # Skyscanner ile tara
            sky_fiyat = skyscanner_aylik_ara(kalkis, varis, yil, ay)
            await asyncio.sleep(0.3)  # Rate limit koruması

            if sky_fiyat and sky_fiyat > 0:
                # Veritabanındaki son fiyatla karşılaştır
                son = son_fiyat_al(kalkis, varis, yil, ay)
                fiyat_kaydet(kalkis, varis, yil, ay, sky_fiyat, sky_fiyat, "EUR", "Skyscanner")

                if son:
                    eski_fiyat = son[0]
                    dusus = eski_fiyat - sky_fiyat
                    yuzde = (dusus / eski_fiyat) * 100 if eski_fiyat > 0 else 0

                    # %5'ten fazla veya 10 EUR'dan fazla düşüş varsa bildir
                    if dusus >= 10 and yuzde >= 5:
                        bildirim_kaydet(kalkis, varis, yil, ay, eski_fiyat, sky_fiyat)
                        dusen_rotalar.append({
                            "rota": f"{kalkis}→{varis}",
                            "ay": f"{yil}-{ay:02d}",
                            "eski": eski_fiyat,
                            "yeni": sky_fiyat,
                            "dusus": dusus,
                            "yuzde": yuzde,
                        })

                # En ucuz ayı takip et
                if rota_min_eur is None or sky_fiyat < rota_min_eur:
                    rota_min_eur = sky_fiyat
                    rota_min_ay = f"{yil}-{ay:02d}"

        if rota_min_eur:
            bulunan_rotalar.append({
                "rota": f"{kalkis}→{varis}",
                "min_eur": rota_min_eur,
                "min_ay": rota_min_ay,
            })
        else:
            bulunamayan_rotalar.append(f"{kalkis}→{varis}")

    # ── BİLDİRİMLER ──────────────────────────

    # 1. Fiyat düşüş bildirimleri (anında)
    for r in dusen_rotalar:
        kalkis_k, varis_k = r['rota'].split("→")
        yil_k = int(r['ay'].split("-")[0])
        ay_k = int(r['ay'].split("-")[1])
        link = skyscanner_link_olustur(kalkis_k, varis_k, yil_k, ay_k)
        mesaj = (
            f"📉 *FİYAT DÜŞTÜ!*\n\n"
            f"✈️ *{r['rota']}* (Gidiş-Dönüş)\n"
            f"📅 {r['ay']}\n\n"
            f"~~{r['eski']:.0f} EUR~~ → *{r['yeni']:.0f} EUR*\n"
            f"💰 *{r['dusus']:.0f} EUR daha ucuz* (-%{r['yuzde']:.0f})\n\n"
            f"🔗 [Skyscanner'da Gör]({link})\n\n"
            f"_Kaynak: Skyscanner_"
        )
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=mesaj,
            parse_mode="Markdown"
        )
        await asyncio.sleep(0.5)

    # 2. Günlük özet raporu
    if bulunan_rotalar:
        bulunan_sirali = sorted(bulunan_rotalar, key=lambda x: x["min_eur"])

        # Başlık mesajı
        baslik = f"☀️ *Günlük Uçuş Raporu* — {bugun.strftime('%d.%m.%Y')}\n"
        baslik += f"🕖 Saat 07:00 taraması tamamlandı\n"
        baslik += "━━━━━━━━━━━━━━━━━━━━\n"
        baslik += f"✅ *{len(bulunan_rotalar)} rotada fiyat bulundu*"
        if dusen_rotalar:
            baslik += f" | 📉 *{len(dusen_rotalar)} rotada düşüş var!*"
        baslik += f"\n_Toplam {len(ROTALAR)} rota × 12 ay tarandı_"

        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=baslik,
            parse_mode="Markdown"
        )
        await asyncio.sleep(0.3)

        # Düşen rotaları set olarak tut (hızlı arama için)
        dusen_rota_set = {f"{d['rota']}" for d in dusen_rotalar}
        dusen_rota_bilgi = {d['rota']: d for d in dusen_rotalar}

        # Düşen rotalar önce, sonra diğerleri
        dusenler = [r for r in bulunan_sirali if r['rota'] in dusen_rota_set]
        digerler = [r for r in bulunan_sirali if r['rota'] not in dusen_rota_set]
        sirali = dusenler + digerler

        # Rotaları 25'er 25'er gönder (Telegram 4096 karakter limiti)
        parca = []
        for r in sirali:
            rota = r['rota']
            kalkis_k, varis_k = rota.split("→")
            yil_k = int(r['min_ay'].split("-")[0])
            ay_k = int(r['min_ay'].split("-")[1])
            link = skyscanner_link_olustur(kalkis_k, varis_k, yil_k, ay_k)

            if rota in dusen_rota_set:
                d = dusen_rota_bilgi[rota]
                satir = (
                    f"📉 `{rota}` — ~~{d['eski']:.0f}~~ → *{r['min_eur']:.0f} EUR* "
                    f"({r['min_ay']}) ↓{d['dusus']:.0f}€ -%{d['yuzde']:.0f}\n"
                    f"🔗 [Skyscanner'da Gör]({link})"
                )
            else:
                satir = (
                    f"✈️ `{rota}` — *{r['min_eur']:.0f} EUR* ({r['min_ay']})\n"
                    f"🔗 [Skyscanner'da Gör]({link})"
                )
            parca.append(satir)

            if len(parca) == 25:
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text="\n".join(parca),
                    parse_mode="Markdown"
                )
                await asyncio.sleep(0.3)
                parca = []

        # Kalan rotalar
        if parca:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text="\n".join(parca),
                parse_mode="Markdown"
            )
            await asyncio.sleep(0.3)

        # Fiyat bulunamayan rotalar
        if bulunamayan_rotalar:
            ozet = f"❌ *Fiyat bulunamayan {len(bulunamayan_rotalar)} rota:*\n"
            ozet += ", ".join(bulunamayan_rotalar)
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=ozet,
                parse_mode="Markdown"
            )

    logger.info(f"Tarama bitti. Bulunan: {len(bulunan_rotalar)}, Düşen: {len(dusen_rotalar)}")


# ── SCHEDULER ─────────────────────────────────

async def gunluk_zamanlayici(context: ContextTypes.DEFAULT_TYPE):
    """Her sabah 07:00'de çalışır."""
    logger.info("Zamanlanmış tarama başlıyor...")
    await tum_rotalari_tara(context.bot)


# ── TELEGRAM KOMUTLARI ────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ *Uçuş Fiyat Takip Sistemi*\n\n"
        f"*{len(ROTALAR)} rota* izleniyor.\n"
        "Her sabah 07:00'de otomatik tarama yapılır.\n\n"
        "*Komutlar:*\n"
        "/rotalar — tüm izlenen rotalar\n"
        "/simdi — hemen tarama başlat\n"
        "/fiyatlar — bugünkü en ucuz fiyatlar\n"
        "/chatid — Chat ID'ni öğren",
        parse_mode="Markdown",
    )


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chat ID'yi göster — TELEGRAM_CHAT_ID için gerekli."""
    await update.message.reply_text(
        f"Chat ID'n: `{update.effective_chat.id}`\n\n"
        "Bu numarayı `bot.py` dosyasındaki `TELEGRAM_CHAT_ID` satırına yaz.",
        parse_mode="Markdown",
    )


async def rotalar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mesaj = f"✈️ *İzlenen {len(ROTALAR)} Rota (Gidiş-Dönüş):*\n\n"
    for i, (k, v) in enumerate(ROTALAR, 1):
        mesaj += f"{i}. `{k} ↔ {v}`\n"
    await update.message.reply_text(mesaj, parse_mode="Markdown")


async def simdi_tara(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 Tarama başlatılıyor...\n"
        f"{len(ROTALAR)} rota × 12 ay taranıyor.\n"
        "⏳ Bu 5-10 dakika sürebilir, sonunda rapor gelecek."
    )
    await tum_rotalari_tara(context.bot)


async def bugunun_fiyatlari(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Veritabanındaki en güncel fiyatları göster."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT kalkis, varis, MIN(min_fiyat_eur), tarih
        FROM fiyatlar
        WHERE created_at >= date('now', '-1 day')
        GROUP BY kalkis, varis
        ORDER BY min_fiyat_eur ASC
        LIMIT 30
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "Henüz fiyat verisi yok.\n/simdi komutuyla tarama başlatabilirsin."
        )
        return

    mesaj = f"💰 *Güncel En Ucuz Fiyatlar*\n"
    mesaj += f"_{datetime.now().strftime('%d.%m.%Y %H:%M')}_\n"
    mesaj += "━━━━━━━━━━━━━━━━━━━━\n\n"

    for k, v, fiyat, tarih in rows:
        mesaj += f"✈️ `{k}↔{v}` — *{fiyat:.0f} EUR* ({tarih})\n"

    await update.message.reply_text(mesaj, parse_mode="Markdown")


async def hata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Hata: {context.error}")


# ── ANA ÇALIŞMA ───────────────────────────────

def main():
    db_baslat()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Komutlar
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("rotalar", rotalar))
    app.add_handler(CommandHandler("simdi", simdi_tara))
    app.add_handler(CommandHandler("fiyatlar", bugunun_fiyatlari))
    app.add_error_handler(hata)

    # Her sabah 07:00'de çalış (UTC+3 için UTC 04:00)
    app.job_queue.run_daily(
        gunluk_zamanlayici,
        time=datetime.strptime("04:00", "%H:%M").time(),  # UTC 04:00 = TR 07:00
        days=(0, 1, 2, 3, 4, 5, 6),
    )

    logger.info(f"Bot başlatıldı. {len(ROTALAR)} rota izleniyor.")
    logger.info("Her sabah 07:00 (TR) tarama yapılacak.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
