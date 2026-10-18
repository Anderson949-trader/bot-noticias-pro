import os
import re
import json
import time
import logging
import threading
import urllib.request
import urllib.error
from datetime import datetime, timedelta

import pytz
import telebot
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bot_fundamental")

TOKEN = "8007552290:AAHH8KQrYklwR6oh8Tjw2_VbUvXs1D8Zd_I"
CHAT_ID = "848594835"

ZONA_HORARIA = pytz.timezone("America/Bogota")
DIVISAS_OBJETIVO = {"EUR", "USD", "JPY", "GBP", "AUD", "CAD", "CHF", "NZD"}
IMPACTOS_A_MONITOREAR = {"High", "Medium"}

URL_CALENDARIO = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
_PAISES_OBJETIVO_IDS = {5: "USD", 4: "GBP", 72: "EUR", 35: "JPY", 25: "AUD", 6: "CAD", 12: "CHF", 43: "NZD"}
_ZONA_FUENTE = pytz.timezone("America/New_York")

AVISOS_PREVIOS_MIN = [15, 5]
REINTENTOS_REACCION_MIN = [3, 8, 15, 30]
INTERVALO_SINCRONIZACION_MIN = 60
ARCHIVO_ESTADO = "estado_calendario.json"

bot = telebot.TeleBot(TOKEN, parse_mode=None)
app = Flask(__name__)

_estado_salud = {
    "arranque": datetime.now(ZONA_HORARIA).isoformat(),
    "ultima_sincronizacion": None,
    "eventos_detectados_hoy": 0,
    "eventos_detectados_semana": 0,
}

_ESTADO_DEFECTO = {"programados": [], "reacciones_enviadas": []}
_lock_archivo = threading.Lock()

def _cargar_estado_disco():
    with _lock_archivo:
        if not os.path.exists(ARCHIVO_ESTADO): return dict(_ESTADO_DEFECTO)
        try:
            with open(ARCHIVO_ESTADO, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: return dict(_ESTADO_DEFECTO)

def _guardar_estado_disco(estado):
    with _lock_archivo:
        try:
            with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f: json.dump(estado, f, ensure_ascii=False, indent=2)
        except Exception: pass

@app.route("/")
def keep_alive(): return "Bot Activo"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

_HEADERS_INVESTING = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://www.investing.com/economic-calendar/",
}

def obtener_calendario():
    cuerpo = "importance%5B%5D=1&importance%5B%5D=2&importance%5B%5D=3&timeZone=8&timeFilter=timeRemain&currentTab=thisWeek" + "".join(f"&country%5B%5D={cid}" for cid in _PAISES_OBJETIVO_IDS)
    try:
        req = urllib.request.Request(URL_CALENDARIO, data=cuerpo.encode("utf-8"), headers=_HEADERS_INVESTING, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore")).get("data", "")
    except Exception as e:
        log.error(f"Error descargando calendario: {e}")
        return []

    eventos = []
    # Extracción por regex limpia sin necesidad de BeautifulSoup
    filas = re.findall(r'<tr[^>]*id="eventRowId_(\d+)"[^>]*data-event-datetime="([^"]+)"[^>]*>(.*?)</tr>', data, re.DOTALL)
    for row_id, dt_text, contenido in filas:
        try:
            fecha_ing = datetime.strptime(dt_text, "%Y/%m/%d %H:%M:%S")
            fecha = _ZONA_FUENTE.localize(fecha_ing)
            
            # Extraer divisa
            match_div = re.search(r'class="flagCur"[^>]*>\s*([A-Z]{3})', contenido)
            if not match_div: continue
            divisa = match_div.group(1).upper()
            
            # Extraer impacto (número de toros)
            toros = len(re.findall(r'<i class="gray(.?)Bull"></i>', contenido))
            impacto = "High" if toros == 3 else ("Medium" if toros == 2 else "Low")
            
            # Extraer título
            match_tit = re.search(r'class="event"[^>]*>.*?<a[^>]*>(.*?)</a>', contenido, re.DOTALL)
            titulo = match_tit.group(1).strip() if match_tit else "Evento macro"
            titulo = re.sub(r'<[^>]+>', '', titulo)
            
            def get_val(suf):
                m = re.search(rf'id="event{suf}_{row_id}"[^>]*>(.*?)<', contenido)
                return m.group(1).strip() if m else ""

            eventos.append({
                "titulo": titulo, "divisa": divisa, "impacto": impacto, "fecha": fecha,
                "pronostico": get_val("Forecast"), "anterior": get_val("Previous"), "actual": get_val("Actual")
            })
        except Exception: continue
    return eventos

def eventos_relevantes(eventos, solo_hoy=False):
    ahora = datetime.now(ZONA_HORARIA)
    res = []
    for ev in eventos:
        if ev["divisa"] not in DIVISAS_OBJETIVO or ev["impacto"] not in IMPACTOS_A_MONITOREAR: continue
        if solo_hoy and ev["fecha"].astimezone(ZONA_HORARIA).date() != ahora.date(): continue
        res.append(ev)
    return res

def _enviar(msg):
    try: bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
    except Exception:
        try: bot.send_message(CHAT_ID, msg.replace("*", "").replace("_", "").replace("`", ""), parse_mode=None)
        except Exception: pass

@bot.message_handler(commands=["start", "ayuda"])
def cmd_start(m): _enviar("🤖 Bot Activo.\nComandos: /hoy, /semana, /estado")

@bot.message_handler(commands=["hoy", "resumen"])
def cmd_hoy(m):
    evs = eventos_relevantes(obtener_calendario(), solo_hoy=True)
    if not evs: _enviar("🌅 No hay noticias relevantes hoy.")
    else:
        txt = [f"🌅 *AGENDA DE HOY ({len(evs)} eventos):*\n"]
        for e in sorted(evs, key=lambda x: x["fecha"]):
            h = e["fecha"].astimezone(ZONA_HORARIA).strftime("%H:%M")
            txt.append(f"• `{h}` | {e['divisa']} | {e['titulo']}")
        _enviar("\n".join(txt))

@bot.message_handler(commands=["semana"])
def cmd_semana(m):
    evs = sorted(eventos_relevantes(obtener_calendario(), solo_hoy=False), key=lambda x: x["fecha"])
    if not evs: _enviar("📅 Sin eventos esta semana.")
    else:
        txt = [f"📅 *SEMANA ({len(evs)} eventos):*\n"]
        for e in evs[:20]:
            h = e["fecha"].astimezone(ZONA_HORARIA).strftime("%a %H:%M")
            txt.append(f"• `{h}` | {e['divisa']} | {e['titulo']}")
        _enviar("\n".join(txt))

@bot.message_handler(commands=["estado"])
def cmd_estado(m): _enviar(f"⚙️ Estado OK.\n• Sincronizado: {_estado_salud['ultima_sincronizacion']}")

scheduler = BackgroundScheduler(timezone=ZONA_HORARIA)

def sincronizar():
    evs = eventos_relevantes(obtener_calendario(), solo_hoy=False)
    _estado_salud["ultima_sincronizacion"] = datetime.now(ZONA_HORARIA).isoformat()
    _estado_salud["eventos_detectados_hoy"] = len([e for e in evs if e["fecha"].astimezone(ZONA_HORARIA).date() == datetime.now(ZONA_HORARIA).date()])
    _estado_salud["eventos_detectados_semana"] = len(evs)

if __name__ == "__main__":
    scheduler.add_job(sincronizar, "interval", minutes=INTERVALO_SINCRONIZACION_MIN, id="sincro")
    scheduler.start()
    sincronizar()

    threading.Thread(target=run_flask, daemon=True).start()
    try: bot.remove_webhook()
    except Exception: pass

    _enviar("✅ *Bot Fundamental Activo y Actualizado.*")

    while True:
        try: bot.infinity_polling(timeout=10, long_polling_timeout=5, skip_pending=True)
        except Exception: time.sleep(15)
