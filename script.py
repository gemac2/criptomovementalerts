import os
import time
import traceback
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo  # Soporte nativo en Python 3.9+
import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv

load_dotenv()

# ====== Configuración de API y Telegram ======
BINANCE_API_KEY = ''
BINANCE_API_SECRET = ''

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ====== Parámetros de Escaneo ======
VARIACION_30M = 5.0
VARIACION_30M_LOWVOL = 7.0
VARIACION_FAST_2M = 2.0
MIN_QUOTE_VOL = 100_000_000  # 100M USDT
TOP_SIMBOLOS = 80
SLEEP_ENTRE_KLINES = 0.1     # Reducido para mayor velocidad
SLEEP_CICLO = 30
REFRESH_SIMBOLOS_CADA = 600  # 10 min

# ====== Configuración Zona Horaria y Estadísticas ======
VET_TIMEZONE = ZoneInfo("America/Caracas")
alert_stats = defaultdict(int)

client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
http = requests.Session()

_simbolos_usdt = []
_simbolos_last_refresh = 0


# ====== Funciones de Estadísticas por Franja Horaria (VET) ======
def get_time_bucket(dt_vet: datetime) -> str:
    """Determina la franja horaria de 4 horas correspondiente en horario VET."""
    hour = dt_vet.hour
    if 0 <= hour < 4:
        return "12:00 AM - 04:00 AM"
    elif 4 <= hour < 8:
        return "04:00 AM - 08:00 AM"
    elif 8 <= hour < 12:
        return "08:00 AM - 12:00 PM"
    elif 12 <= hour < 16:
        return "12:00 PM - 04:00 PM"
    elif 16 <= hour < 20:
        return "04:00 PM - 08:00 PM"
    else:
        return "08:00 PM - 12:00 AM"


def registrar_alerta_horaria() -> str:
    """Registra la alerta en la franja horaria de Venezuela actual y devuelve la franja asignada."""
    now_vet = datetime.now(VET_TIMEZONE)
    bucket = get_time_bucket(now_vet)
    alert_stats[bucket] += 1
    return bucket


def obtener_resumen_estadisticas() -> str:
    """Genera un bloque de texto en formato Markdown para las estadísticas horarias."""
    time_slots = [
        "08:00 AM - 12:00 PM",
        "12:00 PM - 04:00 PM",
        "04:00 PM - 08:00 PM",
        "08:00 PM - 12:00 AM",
        "12:00 AM - 04:00 AM",
        "04:00 AM - 08:00 AM",
    ]
    texto = "📊 *ESTADÍSTICAS DE ALERTAS (VET)*\n"
    for slot in time_slots:
        count = alert_stats.get(slot, 0)
        texto += f"• `{slot}`: *{count}*\n"
    return texto


# ====== Funciones del Bot y Red ======
def send_telegram_alert(message: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        http.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Telegram] Error: {e}")


def cargar_simbolos_usdt():
    global _simbolos_usdt, _simbolos_last_refresh
    ahora = time.time()
    if (ahora - _simbolos_last_refresh > REFRESH_SIMBOLOS_CADA) or not _simbolos_usdt:
        try:
            lista_ticks = client.futures_symbol_ticker()
            _simbolos_usdt = [t['symbol'] for t in lista_ticks if t['symbol'].endswith('USDT')]
            _simbolos_last_refresh = ahora
            print(f"✅ Símbolos USDT actualizados: {len(_simbolos_usdt)}")
        except Exception as e:
            print(f"[ERR] No se pudo cargar símbolos: {e}")
    return _simbolos_usdt


def get_klines_safe(symbol, limit=30):
    """Obtiene klines con reintentos y manejo de errores."""
    try:
        return client.futures_klines(symbol=symbol, interval='1m', limit=limit)
    except BinanceAPIException as e:
        if e.status_code == 429:
            print("⚠️ Rate limit alcanzado. Durmiendo 10s...")
            time.sleep(10)
        return None
    except Exception:
        return None


def human_format(num):
    magnitude = 0
    while abs(num) >= 1000:
        magnitude += 1
        num /= 1000.0
    return f"{num:.2f}{['', 'K', 'M', 'B', 'T'][magnitude]}"


def evaluar_porcentajes(symbol, klines, info_24h):
    if not klines or len(klines) < 30:
        return

    closes = [float(kline[4]) for kline in klines]
    lows = [float(kline[3]) for kline in klines]
    highs = [float(kline[2]) for kline in klines]

    p_inicial_30m = closes[0] 
    p_final = closes[-1]      
    p_inicial_2m = closes[-3] 

    qvol = float(info_24h.get('quoteVolume', 0))
    var_30m = ((p_final - p_inicial_30m) / p_inicial_30m) * 100
    var_2m = ((p_final - p_inicial_2m) / p_inicial_2m) * 100

    msg = ""
    es_pump = False
    
    # Lógica SHORT / LONG 30m
    abs_var_30m = abs(var_30m)
    umbral = VARIACION_30M if qvol > MIN_QUOTE_VOL else VARIACION_30M_LOWVOL
    
    if abs_var_30m >= umbral:
        tipo = "🚀 *PUMP DETECTADO*" if var_30m > 0 else "🔥 *DUMP DETECTADO*"
        msg += f"{tipo}\nSímbolo: #{symbol}\nVar 30m: {var_30m:.2f}%\n"
        es_pump = var_30m > 0

    # Lógica FAST 2m
    if abs(var_2m) >= VARIACION_FAST_2M:
        tipo_rapido = "🚀 *PUMP RÁPIDO*" if var_2m > 0 else "🔥 *DUMP RÁPIDO*"
        msg += f"⚡ {tipo_rapido}\nSímbolo: #{symbol}\nVar 2m: {var_2m:.2f}%\n"
        es_pump = var_2m > 0

    if msg:
        # 1. Registrar conteo estadístico en horario Venezuela
        franja_actual = registrar_alerta_horaria()

        # 2. Anclajes Fibonacci
        min_precio = min(lows)
        max_precio = max(highs)
        impulso = max_precio - min_precio

        msg += f"\n📏 *ANCLAJES DE FIBONACCI*\n"
        msg += f"Swing Low (Mínimo): `{min_precio:.5f}`\n"
        msg += f"Swing High (Máximo): `{max_precio:.5f}`\n"
        msg += f"Tamaño del impulso: `{impulso:.5f}` USDT\n"

        if es_pump:
            fib_382 = max_precio - (impulso * 0.382)
            fib_500 = max_precio - (impulso * 0.500)
            fib_618 = max_precio - (impulso * 0.618)
            
            msg += f"\n🎯 *ZONAS DE PULLBACK (LONG)*\n"
            msg += f"Entrada 1 (38.2%): `{fib_382:.5f}`\n"
            msg += f"Entrada 2 (50.0%): `{fib_500:.5f}`\n"
            msg += f"🚫 Stop Loss (<61.8%): `{fib_618:.5f}`\n"
        else:
            fib_382 = min_precio + (impulso * 0.382)
            fib_500 = min_precio + (impulso * 0.500)
            fib_618 = min_precio + (impulso * 0.618)
            
            msg += f"\n🎯 *ZONAS DE PULLBACK (SHORT)*\n"
            msg += f"Entrada 1 (38.2%): `{fib_382:.5f}`\n"
            msg += f"Entrada 2 (50.0%): `{fib_500:.5f}`\n"
            msg += f"🚫 Stop Loss (>61.8%): `{fib_618:.5f}`\n"

        msg += f"\n💰 Vol 24h: ${human_format(qvol)}\n"
        msg += f"💵 Precio actual: {p_final}\n"
        msg += f"🔗 [Gráfica en Binance](https://www.binance.com/en/futures/{symbol})\n\n"
        
        # 3. Adjuntar desglose estadístico al final del mensaje
        msg += f"🕒 *Franja Alerta:* `{franja_actual}`\n"
        msg += "───────────────\n"
        msg += obtener_resumen_estadisticas()

        print(msg) 
        send_telegram_alert(msg)


def ciclo():
    simbolos = cargar_simbolos_usdt()
    
    info24_list = client.futures_ticker()
    info24_map = {x['symbol']: x for x in info24_list if x['symbol'] in simbolos}

    candidatos = sorted(
        info24_map.values(), 
        key=lambda x: float(x['quoteVolume']), 
        reverse=True
    )[:TOP_SIMBOLOS]

    print(f"--- Iniciando escaneo de {len(candidatos)} tokens ---")

    for item in candidatos:
        symbol = item['symbol']
        klines = get_klines_safe(symbol)
        if klines:
            evaluar_porcentajes(symbol, klines, item)
        time.sleep(SLEEP_ENTRE_KLINES)


if __name__ == "__main__":
    send_telegram_alert("🤖 *Bot de Escaneo Iniciado con Contador VET*")
    while True:
        try:
            ciclo()
        except Exception as e:
            print(f"FATAL: {e}")
            traceback.print_exc()
        time.sleep(SLEEP_CICLO)