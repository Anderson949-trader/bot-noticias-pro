"""
════════════════════════════════════════════════════════════════════════════
 BOT DE TELEGRAM · CALENDARIO ECONÓMICO Y ANÁLISIS FUNDAMENTAL MULTI-ACTIVO
════════════════════════════════════════════════════════════════════════════
"""

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
from bs4 import BeautifulSoup
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("bot_fundamental")

TOKEN = "8007552290:AAHH8KQrYklwR6oh8Tjw2_VbUvXs1D8Zd_I"
CHAT_ID = "848594835"

if not TOKEN or not CHAT_ID:
    raise SystemExit("ERROR: Faltan el TELEGRAM_TOKEN y/o el TELEGRAM_CHAT_ID.")

ZONA_HORARIA = pytz.timezone("America/Bogota")
DIVISAS_OBJETIVO = {"EUR", "USD", "JPY", "GBP", "AUD", "CAD", "CHF", "NZD"}
IMPACTOS_A_MONITOREAR = {"High", "Medium"}
INCLUIR_SECUNDARIOS = True

URL_CALENDARIO = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"

_PAISES_OBJETIVO_IDS = {
    5: "USD", 4: "GBP", 72: "EUR", 35: "JPY",
    25: "AUD", 6: "CAD", 12: "CHF", 43: "NZD",
}

_ZONA_FUENTE = pytz.timezone("America/New_York")
_IMPORTANCIA_A_TEXTO = {3: "High", 2: "Medium", 1: "Low"}

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
    "avisos_programados_activos": 0,
    "ultimo_error": None,
}

MAPA_ACTIVOS = {
    "USD": {
        "indices": "S&P 500 (US500), Nasdaq 100 (US100) y Dow Jones (US30)",
        "acciones_sensibles": "Tecnológicas/Big Tech (AAPL, NVDA, MSFT) y Banca (JPM, BAC)",
        "nota_pais": "Tasas altas en EE.UU. golpean con más fuerza al Nasdaq.",
    },
    "EUR": {
        "indices": "DAX 40 (Alemania), Euro Stoxx 50 y CAC 40",
        "acciones_sensibles": "Industriales/Exportadoras alemanas y Banca europea",
        "nota_pais": "Un euro fuerte resta competitividad al DAX 40.",
    },
    "GBP": {
        "indices": "FTSE 100 (UK100)",
        "acciones_sensibles": "Financieras, Energía y Consumo del Reino Unido",
        "nota_pais": "Empresas del FTSE 100 facturan en dólares.",
    },
    "JPY": {
        "indices": "Nikkei 225 (JP225)",
        "acciones_sensibles": "Exportadoras japonesas (Toyota, Sony)",
        "nota_pais": "Yen fuerte suele presionar a la baja al Nikkei 225.",
    },
    "AUD": {
        "indices": "ASX 200 (AUS200)",
        "acciones_sensibles": "Mineras y Materias Primas (BHP, Rio Tinto)",
        "nota_pais": "Sensible al ciclo de materias primas y China.",
    },
    "CAD": {
        "indices": "S&P/TSX 60 (Canadá)",
        "acciones_sensibles": "Sector Energético/Petrolero y Bancos",
        "nota_pais": "Correlacionado con los precios del petróleo (WTI).",
    },
    "CHF": {
        "indices": "SMI 20 (Suiza)",
        "acciones_sensibles": "Farmacéuticas (Nestlé, Roche, Novartis)",
        "nota_pais": "Actúa como activo refugio.",
    },
    "NZD": {
        "indices": "NZX 50",
        "acciones_sensibles": "Agroindustria y Exportadoras",
        "nota_pais": "Sensible al apetito global por el riesgo.",
    },
}

_ESTADO_DEFECTO = {"programados": [], "reacciones_enviadas": []}
_lock_archivo = threading.Lock()

def _cargar_estado_disco():
    with _lock_archivo:
        if not os.path.exists(ARCHIVO_ESTADO):
            return dict(_ESTADO_DEFECTO)
        try:
            with open(ARCHIVO_ESTADO, "r", encoding="utf-8") as f:
                estado = json.load(f)
        except (json.JSONDecodeError, OSError):
            return dict(_ESTADO_DEFECTO)
        estado.setdefault("programados", [])
        estado.setdefault("reacciones_enviadas", [])
        return estado

def _guardar_estado_disco(estado):
    with _lock_archivo:
        try:
            with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
                json.dump(estado, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

def _ya_programado(estado, clave):
    return clave in estado["programados"]

def _marcar_programado(estado, clave):
    estado["programados"].append(clave)
    estado["programados"] = estado["programados"][-800:]
    _guardar_estado_disco(estado)

def _ya_reaccion_enviada(clave):
    estado = _cargar_estado_disco()
    return clave in estado["reacciones_enviadas"]

def _marcar_reaccion_enviada(clave):
    estado = _cargar_estado_disco()
    estado["reacciones_enviadas"].append(clave)
    estado["reacciones_enviadas"] = estado["reacciones_enviadas"][-800:]
    _guardar_estado_disco(estado)

@app.route("/")
def keep_alive():
    return "Bot Activo"

def run_flask():
    puerto = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=puerto)

_CACHE_CALENDARIO = {"eventos": None, "momento": None}
_CACHE_TTL_SEGUNDOS = 120
_lock_cache_calendario = threading.Lock()

_HEADERS_INVESTING = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://www.investing.com/economic-calendar/",
}

def _parsear_fila_evento(fila):
    dt_texto = fila.get("data-event-datetime")
    if not dt_texto:
        return None
    try:
        fecha_ingenua = datetime.strptime(dt_texto, "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None
    fecha = _ZONA_FUENTE.localize(fecha_ingenua)

    celda_pais = fila.find("td", class_="flagCur")
    texto_pais = celda_pais.get_text().replace("\xa0", " ") if celda_pais else ""
    partes = texto_pais.split()
    divisa = partes[-1].upper() if partes else ""

    celda_sentimiento = fila.find("td", class_="sentiment")
    n_toros = len(celda_sentimiento.find_all("i")) if celda_sentimiento else 0
    impacto = _IMPORTANCIA_A_TEXTO.get(n_toros, "Low")

    celda_evento = fila.find("td", class_="event")
    enlace = celda_evento.find("a") if celda_evento else None
    titulo_crudo = (enlace.get_text() if enlace else (celda_evento.get_text() if celda_evento else ""))
    titulo = re.sub(r"\s*\([^)]*\)\s*$", "", titulo_crudo).strip()

    def _texto_celda(sufijo):
        celda = fila.find("td", id=re.compile(rf"^event{sufijo}_\d+$"))
        return celda.get_text(strip=True) if celda else ""

    return {
        "titulo": titulo, "divisa": divisa, "impacto": impacto, "fecha": fecha,
        "pronostico": _texto_celda("Forecast"), "anterior": _texto_celda("Previous"),
        "actual": _texto_celda("Actual"),
    }

def obtener_calendario():
    with _lock_cache_calendario:
        momento_cache = _CACHE_CALENDARIO["momento"]
        if momento_cache is not None:
            if (datetime.now(pytz.utc) - momento_cache).total_seconds() < _CACHE_TTL_SEGUNDOS:
                return _CACHE_CALENDARIO["eventos"]

        cuerpo = (
            "importance%5B%5D=1&importance%5B%5D=2&importance%5B%5D=3"
            "&timeZone=8&timeFilter=timeRemain&currentTab=thisWeek"
            + "".join(f"&country%5B%5D={cid}" for cid in _PAISES_OBJETIVO_IDS)
        )
        try:
            peticion = urllib.request.Request(
                URL_CALENDARIO, data=cuerpo.encode("utf-8"),
                headers=_HEADERS_INVESTING, method="POST",
            )
            with urllib.request.urlopen(peticion, timeout=20) as respuesta:
                html = json.loads(respuesta.read().decode("utf-8", errors="ignore")).get("data", "")
        except (urllib.error.URLError, ValueError) as e:
            _estado_salud["ultimo_error"] = f"calendario: {e}"
            return []

        eventos = []
        soup = BeautifulSoup(html, "html.parser")
        for fila in soup.find_all("tr", class_="js-event-item"):
            try:
                evento = _parsear_fila_evento(fila)
                if evento:
                    eventos.append(evento)
            except Exception:
                continue

        _CACHE_CALENDARIO["eventos"] = eventos
        _CACHE_CALENDARIO["momento"] = datetime.now(pytz.utc)
        return eventos

CATEGORIAS = {
    "tasas": {
        "claves": ["tasa", "rate", "fomc", "ecb", "boe", "rba", "boc", "snb", "dot plot"],
        "nombre": "Decisión de tasas", "nivel": "clave", "alcista_si_sube": True, "bolsa_si_sube": "bajista",
        "razon_alcista": "tasas restrictivas atraen capital a la divisa",
        "razon_bajista": "recorte de tasas resta atractivo a la moneda",
        "efecto_bolsa_sube": "BAJISTA para índices por crédito caro",
        "efecto_bolsa_baja": "ALCISTA para índices por financiamiento barato",
    },
    "ipc": {
        "claves": ["ipc", "cpi", "inflación", "pce", "ppi", "earnings"],
        "nombre": "Inflación y Precios", "nivel": "clave", "alcista_si_sube": True, "bolsa_si_sube": "bajista",
        "razon_alcista": "inflación alta obliga a mantener tasas altas",
        "razon_bajista": "inflación baja acerca recortes de tasas",
        "efecto_bolsa_sube": "BAJISTA para índices por temor a tasas altas",
        "efecto_bolsa_baja": "ALCISTA para índices por alivio monetario",
    },
    "pib": {
        "claves": ["pib", "gdp"],
        "nombre": "PIB", "nivel": "clave", "alcista_si_sube": True, "bolsa_si_sube": "alcista",
        "razon_alcista": "economía fuerte apoya a la divisa",
        "razon_bajista": "crecimiento débil debilita la moneda",
        "efecto_bolsa_sube": "ALCISTA para índices y cíclicas",
        "efecto_bolsa_baja": "BAJISTA por menores utilidades",
    },
    "empleo": {
        "claves": ["nóminas", "nfp", "payroll", "employment", "adp", "jolts"],
        "nombre": "Empleo (NFP)", "nivel": "clave", "alcista_si_sube": True, "bolsa_si_sube": "mixto",
        "razon_alcista": "empleo sólido sostiene tasas altas",
        "razon_bajista": "empleo débil anticipa recortes",
        "efecto_bolsa_sube": "MIXTO: apoya consumo pero retrasa recortes",
        "efecto_bolsa_baja": "MIXTO: rebote por tasas bajas o caída por recesión",
    },
    "desempleo_tasa": {
        "claves": ["tasa de desempleo", "unemployment rate"],
        "nombre": "Tasa de desempleo", "nivel": "clave", "alcista_si_sube": False, "bolsa_si_sube": "mixto",
        "razon_alcista": "desempleo bajo presiona salarios y divisa al alza",
        "razon_bajista": "desempleo alto obliga a bajar tasas",
        "efecto_bolsa_sube": "PRECAUCIÓN en bolsa", "efecto_bolsa_baja": "SOLIDEZ macro",
    },
    "solicitudes_desempleo": {
        "claves": ["jobless claims", "claims", "subsidio"],
        "nombre": "Solicitudes de subsidio", "nivel": "secundario", "alcista_si_sube": False, "bolsa_si_sube": "mixto",
        "razon_alcista": "menos despidos apoyan la divisa",
        "razon_bajista": "más despidos pesan sobre la moneda",
        "efecto_bolsa_sube": "Alivia bonos y da respiro a tech", "efecto_bolsa_baja": "Favorece cíclicos",
    },
    "pmi": {
        "claves": ["pmi", "ism", "manufacturing", "services"],
        "nombre": "PMI / ISM", "nivel": "clave", "alcista_si_sube": True, "bolsa_si_sube": "alcista",
        "razon_alcista": "PMI > 50 indica expansión",
        "razon_bajista": "PMI < 50 indica contracción",
        "efecto_bolsa_sube": "ALCISTA para industriales", "efecto_bolsa_baja": "BAJISTA por desaceleración",
    },
    "ventas_minoristas": {
        "claves": ["ventas minoristas", "retail sales"],
        "nombre": "Ventas minoristas", "nivel": "secundario", "alcista_si_sube": True, "bolsa_si_sube": "alcista",
        "razon_alcista": "consumo fuerte impulsa divisa",
        "razon_bajista": "consumo débil frena crecimiento",
        "efecto_bolsa_sube": "ALCISTA para retail", "efecto_bolsa_baja": "BAJISTA para comercio",
    },
    "confianza_consumidor": {
        "claves": ["confianza", "sentiment", "zew", "ifo"],
        "nombre": "Confianza consumidor", "nivel": "secundario", "alcista_si_sube": True, "bolsa_si_sube": "alcista",
        "razon_alcista": "optimismo favorece divisa",
        "razon_bajista": "pesimismo presiona divisa",
        "efecto_bolsa_sube": "ALCISTA moderado", "efecto_bolsa_baja": "BAJISTA moderado",
    },
    "balanza_comercial": {
        "claves": ["balanza comercial", "trade balance"],
        "nombre": "Balanza comercial", "nivel": "secundario", "alcista_si_sube": True, "bolsa_si_sube": "alcista",
        "razon_alcista": "mayor superávit demanda moneda local",
        "razon_bajista": "déficit comercial implica salida de divisas",
        "efecto_bolsa_sube": "Positivo para exportadoras", "efecto_bolsa_baja": "Debilidad industrial",
    },
    "vivienda": {
        "claves": ["housing", "permits", "starts", "home sales"],
        "nombre": "Sector vivienda", "nivel": "secundario", "alcista_si_sube": True, "bolsa_si_sube": "alcista",
        "razon_alcista": "vivienda resiste tasas altas",
        "razon_bajista": "tasas altas frenan construcción",
        "efecto_bolsa_sube": "ALCISTA para constructoras", "efecto_bolsa_baja": "BAJISTA inmobiliario",
    },
    "discurso": {
        "claves": ["discurso", "speech", "speaks", "remarks", "testimony", "press conference"],
        "nombre": "Discurso banquero central", "nivel": "clave", "alcista_si_sube": None, "bolsa_si_sube": None,
        "razon_alcista": None, "razon_bajista": None,
    },
    "actas": {
        "claves": ["actas", "minutes"],
        "nombre": "Actas de política monetaria", "nivel": "clave", "alcista_si_sube": None, "bolsa_si_sube": None,
        "razon_alcista": None, "razon_bajista": None,
    },
}

CATEGORIA_DEFECTO = {
    "nombre": "Indicador macroeconómico", "nivel": "descartar",
    "alcista_si_sube": None, "bolsa_si_sube": None, "razon_alcista": None, "razon_bajista": None,
}

_PATRON_PROYECCION_SUELTA = re.compile(r"(interest rate|rate) projection.*\b(year|qtr|quarter)\b", re.IGNORECASE)

def clasificar_evento(titulo):
    if _PATRON_PROYECCION_SUELTA.search(titulo or ""):
        return CATEGORIA_DEFECTO
    tn = (titulo or "").lower()
    for datos in CATEGORIAS.values():
        if any(clave in tn for clave in datos["claves"]):
            return datos
    return CATEGORIA_DEFECTO

def _nivel_de_relevancia(titulo):
    return clasificar_evento(titulo).get("nivel", "descartar")

def eventos_relevantes(eventos, solo_hoy=False):
    ahora_bogota = datetime.now(ZONA_HORARIA)
    resultado = []
    for ev in eventos:
        if ev["divisa"] not in DIVISAS_OBJETIVO:
            continue
        nivel = _nivel_de_relevancia(ev["titulo"])
        if nivel == "descartar" or (nivel == "secundario" and not INCLUIR_SECUNDARIOS):
            continue
        if ev["impacto"] not in IMPACTOS_A_MONITOREAR:
            continue
        if solo_hoy and ev["fecha"].astimezone(ZONA_HORARIA).date() != ahora_bogota.date():
            continue
        resultado.append(ev)
    return resultado

def _a_numero(texto):
    if not texto:
        return None
    limpio = texto.strip().replace("%", "").replace(",", "").replace("$", "").strip()
    if not limpio:
        return None
    mult = 1.0
    char = limpio[-1].upper()
    if char == "K": mult, limpio = 1_000.0, limpio[:-1]
    elif char == "M": mult, limpio = 1_000_000.0, limpio[:-1]
    elif char == "B": mult, limpio = 1_000_000_000.0, limpio[:-1]
    try:
        return float(limpio) * mult
    except ValueError:
        return None

def evaluar_descuento_previo(evento):
    cat = clasificar_evento(evento.get("titulo", ""))
    if cat.get("alcista_si_sube") is None:
        return None
    pron, ant = _a_numero(evento.get("pronostico")), _a_numero(evento.get("anterior"))
    if pron is None or ant is None:
        return None
    diff = pron - ant
    if abs(diff) / max(abs(ant), abs(pron), 1e-6) < 0.015:
        return "⚖️ *Priced-in:* Consenso similar al anterior. Movimiento limpio ante sorpresas."
    alcista_divisa = (diff > 0) if cat["alcista_si_sube"] else not (diff > 0)
    if alcista_divisa:
        return f"📉 *Priced-in:* Consenso favorable para {evento['divisa']}. Descontado en precio; cuidado con 'vender la noticia'."
    return f"📈 *Priced-in:* Consenso débil para {evento['divisa']}. Pesimismo descontado; sorpresa alcista genera rebote fuerte."

def analizar_resultado(evento):
    cat = clasificar_evento(evento.get("titulo", ""))
    if cat.get("alcista_si_sube") is None:
        return None
    actual, pron = _a_numero(evento.get("actual")), _a_numero(evento.get("pronostico"))
    if actual is None or pron is None:
        return None
    diff = actual - pron
    divisa = evento.get("divisa", "")
    indices_txt = MAPA_ACTIVOS.get(divisa, {}).get("indices", f"Índices de {divisa}")

    if abs(diff) < 1e-9 or abs(diff) / max(abs(pron), 1e-6) < 0.008:
        return {
            "direccion": "neutral", "razon_divisa": "en línea con el consenso",
            "impacto_bolsa": f"NEUTRAL para {indices_txt}",
            "descuento_txt": "⚖️ *Ya descontado:* Sin sorpresa real, movimiento limitado.",
            "confirmacion": None,
        }

    por_encima = diff > 0
    es_alcista = por_encima if cat["alcista_si_sube"] else not por_encima
    efecto_bolsa = (cat.get("efecto_bolsa_sube") if por_encima else cat.get("efecto_bolsa_baja")) or "Mixto"

    return {
        "direccion": "alcista" if es_alcista else "bajista",
        "razon_divisa": cat["razon_alcista"] if es_alcista else cat["razon_bajista"],
        "impacto_bolsa": f"{efecto_bolsa} ({indices_txt})",
        "descuento_txt": "🚀 Sorpresa fuerte detectada frente al consenso.",
        "confirmacion": None,
    }

_IMPACTO_ES = {"High": "Alto 🔴", "Medium": "Medio 🟠", "Low": "Bajo 🟡"}
def traducir_impacto(i): return _IMPACTO_ES.get(i, i)

_TRADUCCIONES = [
    (r"\bNon-Farm Payrolls\b", "Nóminas No Agrícolas (NFP)"),
    (r"\bUnemployment Rate\b", "Tasa de Desempleo"),
    (r"\bCore CPI\b", "IPC Subyacente"),
    (r"\bCPI\b", "IPC"),
    (r"\bGDP\b", "PIB"),
    (r"\bRetail Sales\b", "Ventas Minoristas"),
    (r"\bInterest Rate Decision\b", "Decisión de Tipos"),
]
def traducir_titulo(t):
    res = t or ""
    for pat, rep in _TRADUCCIONES:
        res = re.sub(pat, rep, res, flags=re.IGNORECASE)
    return res

def _escapar(t): return (t or "").replace("_", "-").replace("*", "").replace("`", "'")
def _bandera(d): return {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "AUD": "🇦🇺", "CAD": "🇨🇦", "CHF": "🇨🇭", "NZD": "🇳🇿"}.get(d, "")

def mensaje_previo(grupo, minutos):
    divisa = grupo[0]["divisa"]
    band = _bandera(divisa)
    hora = grupo[0]["fecha"].astimezone(ZONA_HORARIA).strftime("%H:%M")
    ev = grupo[0]
    tit = _escapar(traducir_titulo(ev["titulo"]))
    
    base = f"⚠️ *En {minutos} min:* {tit} ({band} {divisa})\nImpacto: {traducir_impacto(ev['impacto'])} · {hora}"
    if ev['pronostico']:
        base += f"\nPronóstico: {ev['pronostico']} | Anterior: {ev['anterior']}"
    
    desc = evaluar_descuento_previo(ev)
    if desc:
        base += f"\n\n{desc}"
    return base

def mensaje_reaccion(evento, analisis):
    tit = _escapar(traducir_titulo(evento["titulo"]))
    divisa = evento["divisa"]
    band = _bandera(divisa)
    emoji = {"alcista": "🟢", "bajista": "🔴", "neutral": "⚪"}[analisis["direccion"]]
    
    return (
        f"{emoji} *PUBLICADO: {tit}* ({band} {divisa})\n"
        f"📊 *Actual:* `{evento['actual']}` | *Pronóstico:* `{evento['pronostico']}`\n\n"
        f"💱 *Divisa:* *{analisis['direccion'].upper()}* — {analisis['razon_divisa']}.\n"
        f"📈 *Bolsa:* {analisis['impacto_bolsa']}.\n\n{analisis['descuento_txt']}"
    )

def mensaje_reporte_matutino(eventos):
    if not eventos:
        return "🌅 No hay noticias relevantes hoy. Usa /semana para ver la agenda."
    lineas = [f"🌅 *AGENDA DEL DÍA ({len(eventos)} eventos):*\n"]
    for ev in sorted(eventos, key=lambda e: e["fecha"]):
        h = ev["fecha"].astimezone(ZONA_HORARIA).strftime("%H:%M")
        lineas.append(f"• `{h}` | {_bandera(ev['divisa'])} {ev['divisa']} | {_escapar(traducir_titulo(ev['titulo']))}")
    return "\n".join(lineas)

def _enviar(msg, destino=None):
    dest = destino or CHAT_ID
    try:
        bot.send_message(dest, msg, parse_mode="Markdown")
    except Exception:
        try:
            bot.send_message(dest, msg.replace("*", "").replace("_", "").replace("`", ""), parse_mode=None)
        except Exception:
            pass

def job_aviso_previo(g, m): _enviar(mensaje_previo(g, m))
def job_publicacion(g): pass

def job_reaccion(ev):
    clave = f"{ev['divisa']}|{ev['titulo']}|{ev['fecha'].isoformat()}"
    if _ya_reaccion_enviada(clave): return
    actualizado = ev
    for e in obtener_calendario():
        if e["titulo"] == ev["titulo"] and e["divisa"] == ev["divisa"] and e["fecha"] == ev["fecha"]:
            actualizado = e
            break
    analisis = analizar_resultado(actualizado)
    if analisis:
        _marcar_reaccion_enviada(clave)
        _enviar(mensaje_reaccion(actualizado, analisis))

def job_reporte_matutino():
    evs = eventos_relevantes(obtener_calendario(), solo_hoy=True)
    _enviar(mensaje_reporte_matutino(evs))

@bot.message_handler(commands=["start", "ayuda", "help"])
def cmd_start(m): _enviar("🤖 Bot Activo.\nComandos: /hoy, /semana, /estado", m.chat.id)

@bot.message_handler(commands=["hoy", "resumen"])
def cmd_hoy(m):
    evs = eventos_relevantes(obtener_calendario(), solo_hoy=True)
    _enviar(mensaje_reporte_matutino(evs), m.chat.id)

@bot.message_handler(commands=["semana"])
def cmd_semana(m):
    evs = sorted(eventos_relevantes(obtener_calendario(), solo_hoy=False), key=lambda e: e["fecha"])
    if not evs:
        _enviar("📅 Sin eventos esta semana.", m.chat.id)
        return
    lineas = [f"📅 *SEMANA ({len(evs)} eventos):*\n"]
    for ev in evs[:25]:
        h = ev["fecha"].astimezone(ZONA_HORARIA).strftime("%a %H:%M")
        lineas.append(f"• `{h}` | {_bandera(ev['divisa'])} {ev['divisa']} | {_escapar(traducir_titulo(ev['titulo']))}")
    _enviar("\n".join(lineas), m.chat.id)

@bot.message_handler(commands=["estado"])
def cmd_estado(m):
    _enviar(f"⚙️ Estado:\n• Sincronizado: {_estado_salud['ultima_sincronizacion']}\n• Alertas activas: {len(scheduler.get_jobs())}", m.chat.id)

scheduler = BackgroundScheduler(timezone=ZONA_HORARIA)

def sincronizar_calendario():
    ahora = datetime.now(pytz.utc)
    estado = _cargar_estado_disco()
    relevantes = eventos_relevantes(obtener_calendario(), solo_hoy=False)
    _estado_salud["eventos_detectados_hoy"] = len(eventos_relevantes(relevantes, solo_hoy=True))
    _estado_salud["eventos_detectados_semana"] = len(relevantes)
    
    grupos = {}
    for ev in relevantes:
        grupos.setdefault((ev["divisa"], ev["fecha"]), []).append(ev)

    for (divisa, fecha), grupo in grupos.items():
        clave_grupo = f"{divisa}|{fecha.isoformat()}|grupo"
        if not _ya_programado(estado, clave_grupo):
            if fecha >= ahora:
                for min_prev in AVISOS_PREVIOS_MIN:
                    momento = fecha - timedelta(minutes=min_prev)
                    if momento > ahora:
                        scheduler.add_job(job_aviso_previo, "date", run_date=momento, args=[grupo, min_prev], id=f"{clave_grupo}|p{min_prev}", replace_existing=True)
            _marcar_programado(estado, clave_grupo)

        for ev in grupo:
            clave_ev = f"{ev['divisa']}|{ev['titulo']}|{ev['fecha'].isoformat()}"
            if _ya_programado(estado, clave_ev): continue
            for idx, m_post in enumerate(REINTENTOS_REACCION_MIN):
                scheduler.add_job(job_reaccion, "date", run_date=ev["fecha"] + timedelta(minutes=m_post), args=[ev], id=f"{clave_ev}|r{idx}", replace_existing=True)
            _marcar_programado(estado, clave_ev)

    _estado_salud["ultima_sincronizacion"] = datetime.now(ZONA_HORARIA).isoformat()
    _estado_salud["avisos_programados_activos"] = len(scheduler.get_jobs())

if __name__ == "__main__":
    scheduler.add_job(job_reporte_matutino, "cron", hour=6, minute=0, id="reporte_matutino")
    scheduler.add_job(sincronizar_calendario, "interval", minutes=INTERVALO_SINCRONIZACION_MIN, id="sincro")
    scheduler.start()
    sincronizar_calendario()

    threading.Thread(target=run_flask, daemon=True).start()
    try: bot.remove_webhook()
    except Exception: pass

    _enviar("✅ *Bot Fundamental Activo.*")
    
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5, skip_pending=True)
        except Exception as e:
            _estado_salud["ultimo_error"] = str(e)
            time.sleep(15)
