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

# Variable para evitar envíos duplicados en el mismo minuto de cierre
_ultimo_reporte_enviado = ""

# Fecha del último reinicio diario de estadísticas
_ultimo_reset_diario = ""

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
    texto = "📊 *REPORTE DE VOLATILIDAD - ESTADÍSTICAS (VET)*\n"
    texto += "───────────────\n"
    total_alertas = 0
    for slot in time_slots:
        count = alert_stats.get(slot, 0)
        total_alertas += count
        texto += f"• `{slot}`: *{count}* alertas\n"
    texto += f"\n📈 *Total acumulado:* *{total_alertas}* alertas"
    return texto


def verificar_envio_reporte_programado():
    """
    Verifica si falta 1 minuto para el cambio de franja horaria (ej: 03:59, 07:59, 11:59, 15:59, 19:59, 23:59 VET)
    y envía el resumen por Telegram si aún no se ha enviado para esa marca de tiempo.
    """
    global _ultimo_reporte_enviado
    
    now_vet = datetime.now(VET_TIMEZONE)
    # Lista de horas en las que se debe gatillar el reporte (las 3:59, 7:59, 11:59, 15:59, 19:59, 23:59)
    horas_objetivo = [3, 7, 11, 15, 19, 23]
    
    if now_vet.hour in horas_objetivo and now_vet.minute == 59:
        # Identificador único del minuto actual (ej: "2026-10-01-15:59")
        clave_minuto = now_vet.strftime("%Y-%m-%d-%H:%M")
        
        if _ultimo_reporte_enviado != clave_minuto:
            msg_reporte = obtener_resumen_estadisticas()
            print(f"\n[PROGRAMADOR] Enviando reporte de cierre de bloque VET ({clave_minuto})...")
            send_telegram_alert(msg_reporte)
            _ultimo_reporte_enviado = clave_minuto

            # Si es el último reporte del día (23:59), reiniciar todos los contadores
            if now_vet.hour == 23:
                alert_stats.clear()
                _ultimo_reset_diario = now_vet.strftime("%Y-%m-%d")
                print("[RESET] Contadores de estadísticas reiniciados (nuevo día VET).")

    # Respaldo: si el bot se reinició y perdió el reporte de las 23:59,
    # también reiniciar al cruzar la medianoche.
    ahora_vet = datetime.now(VET_TIMEZONE)
    hoy = ahora_vet.strftime("%Y-%m-%d")
    if ahora_vet.hour == 0 and _ultimo_reset_diario != hoy:
        if alert_stats:
            print("[RESET] Contadores reiniciados por cambio de día (respaldo medianoche).")
        alert_stats.clear()
        _ultimo_reset_diario = hoy


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

    p_inicial_30m = closes[0] 
    p_final = closes[-1]      
    p_inicial_2m = closes[-3] 

    qvol = float(info_24h.get('quoteVolume', 0))
    var_30m = ((p_final - p_inicial_30m) / p_inicial_30m) * 100
    var_2m = ((p_final - p_inicial_2m) / p_inicial_2m) * 100

    msg = ""
    
    # Lógica SHORT / LONG 30m
    abs_var_30m = abs(var_30m)
    umbral = VARIACION_30M if qvol > MIN_QUOTE_VOL else VARIACION_30M_LOWVOL
    
    if abs_var_30m >= umbral:
        tipo = "🚀 *PUMP DETECTADO*" if var_30m > 0 else "🔥 *DUMP DETECTADO*"
        msg += f"{tipo}\nSímbolo: #{symbol}\nVar 30m: {var_30m:.2f}%\n"

    # Lógica FAST 2m
    if abs(var_2m) >= VARIACION_FAST_2M:
        tipo_rapido = "🚀 *PUMP RÁPIDO*" if var_2m > 0 else "🔥 *DUMP RÁPIDO*"
        msg += f"⚡ {tipo_rapido}\nSímbolo: #{symbol}\nVar 2m: {var_2m:.2f}%\n"

    if msg:
        # Incrementa el conteo interno silenciosamente sin adjuntar el bloque largo
        franja_actual = registrar_alerta_horaria()

        # Alerta de mercado limpia e instantánea
        msg += f"\n💰 Vol 24h: ${human_format(qvol)}\n"
        msg += f"💵 Precio actual: {p_final}\n"
        msg += f"🕒 Franja: `{franja_actual}`\n"
        msg += f"🔗 [Gráfica en Binance](https://www.binance.com/en/futures/{symbol})"

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
    send_telegram_alert("🤖 *Bot de Escaneo Iniciado con Reportes Programados VET*")
    while True:
        try:
            # 1. Escanear el mercado
            ciclo()
            
            # 2. Verificar si toca enviar el reporte de resumen (1 minuto antes de cambiar de franja)
            verificar_envio_reporte_programado()
            
        except Exception as e:
            print(f"FATAL: {e}")
            traceback.print_exc()
            
        time.sleep(SLEEP_CICLO)