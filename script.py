import time
import os
import traceback
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

client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
http = requests.Session()

_simbolos_usdt = []
_simbolos_last_refresh = 0

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
        # Usamos 1m por defecto según tu lógica de 30m y 2m
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

    # Extraer el array de precios de cierre (close), mínimos (low) y máximos (high)
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
        # --- CÁLCULO DE FIBONACCI ---
        # Buscamos el mínimo y máximo de las últimas 30 velas para medir el impulso
        min_precio = min(lows)
        max_precio = max(highs)
        impulso = max_precio - min_precio

        msg += f"\n📏 *ANCLAJES DE FIBONACCI*\n"
        msg += f"Swing Low (Mínimo): `{min_precio:.5f}`\n"
        msg += f"Swing High (Máximo): `{max_precio:.5f}`\n"
        msg += f"Tamaño del impulso: `{impulso:.5f}` USDT\n"

        if es_pump:
            # Si es Pump, calculamos los retrocesos hacia abajo desde el máximo
            fib_382 = max_precio - (impulso * 0.382)
            fib_500 = max_precio - (impulso * 0.500)
            fib_618 = max_precio - (impulso * 0.618)
            
            msg += f"\n🎯 *ZONAS DE PULLBACK (LONG)*\n"
            msg += f"Entrada 1 (38.2%): `{fib_382:.5f}`\n"
            msg += f"Entrada 2 (50.0%): `{fib_500:.5f}`\n"
            msg += f"🚫 Stop Loss (<61.8%): `{fib_618:.5f}`\n"
        else:
            # Si es Dump, calculamos los retrocesos hacia arriba desde el mínimo (para Short)
            fib_382 = min_precio + (impulso * 0.382)
            fib_500 = min_precio + (impulso * 0.500)
            fib_618 = min_precio + (impulso * 0.618)
            
            msg += f"\n🎯 *ZONAS DE PULLBACK (SHORT)*\n"
            msg += f"Entrada 1 (38.2%): `{fib_382:.5f}`\n"
            msg += f"Entrada 2 (50.0%): `{fib_500:.5f}`\n"
            msg += f"🚫 Stop Loss (>61.8%): `{fib_618:.5f}`\n"

        msg += f"\n💰 Vol 24h: ${human_format(qvol)}\n"
        msg += f"💵 Precio actual: {p_final}\n"
        msg += f"🔗 [Gráfica en Binance](https://www.binance.com/en/futures/{symbol})"
        
        print(msg) 
        send_telegram_alert(msg)

def ciclo():
    simbolos = cargar_simbolos_usdt()
    
    # Obtener toda la info 24h en un solo request (Weight: 1 o 40 dependiendo del endpoint)
    # futures_ticker() es eficiente para traer todo el mercado
    info24_list = client.futures_ticker()
    info24_map = {x['symbol']: x for x in info24_list if x['symbol'] in simbolos}

    # Ordenar por volumen y tomar el top
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
    send_telegram_alert("🤖 *Bot de Escaneo Iniciado*")
    while True:
        try:
            ciclo()
        except Exception as e:
            print(f"FATAL: {e}")
            traceback.print_exc()
        time.sleep(SLEEP_CICLO)