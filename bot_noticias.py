"""
════════════════════════════════════════════════════════════════════════════
 BOT DE TELEGRAM · CALENDARIO ECONÓMICO Y ANÁLISIS FUNDAMENTAL MULTI-ACTIVO
 (Divisas, Índices Bursátiles, Sectores de Acciones y Descuento de Mercado)
════════════════════════════════════════════════════════════════════════════

CONFIGURACIÓN DE VARIABLES DE ENTORNO:
  TELEGRAM_TOKEN   -> 8007552290:AAHH8KQrYklwR6oh8Tjw2_VbUvXs1D8Zd_I
  TELEGRAM_CHAT_ID -> 848594835

COMANDOS DISPONIBLES EN TELEGRAM:
  /hoy o /resumen  -> Envía el panorama fundamental del día y qué esperar
  /estado          -> Muestra el estado técnico del bot y próximas alertas
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

# ══════════════════════════════════════════════════════════════════════════
# 1. CONFIGURACIÓN GENERAL
# ══════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("bot_fundamental")

# SEGURIDAD: Configura TELEGRAM_TOKEN y TELEGRAM_CHAT_ID en tus variables
# de entorno (Render -> Environment o archivo .env local).
TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not TOKEN or not CHAT_ID:
    raise SystemExit(
        "ERROR: Faltan las variables de entorno TELEGRAM_TOKEN y/o TELEGRAM_CHAT_ID.\n"
        "Configúralas en tu entorno antes de arrancar el bot."
    )

ZONA_HORARIA = pytz.timezone("America/Bogota")

# Divisas principales a monitorear
DIVISAS_OBJETIVO = {"EUR", "USD", "JPY", "GBP", "AUD", "CAD", "CHF", "NZD"}

# AHORA MONITOREA IMPACTO MEDIO (2 toros) Y ALTO (3 toros)
IMPACTOS_A_MONITOREAR = {"High", "Medium"}

# En True para incluir también ventas minoristas, confianza del consumidor,
# subsidios de desempleo, balanza comercial y vivienda (claves en impacto medio).
INCLUIR_SECUNDARIOS = True

# ── FUENTE: Investing.com ──
URL_CALENDARIO = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"

_PAISES_OBJETIVO_IDS = {
    5: "USD", 4: "GBP", 72: "EUR", 35: "JPY",
    25: "AUD", 6: "CAD", 12: "CHF", 43: "NZD",
}

_ZONA_FUENTE = pytz.timezone("America/New_York")
_IMPORTANCIA_A_TEXTO = {3: "High", 2: "Medium", 1: "Low"}

# En noticias de Alto impacto avisa 15 y 5 min antes; en Medio solo 15 min
# antes para darte contexto sin saturar tu Telegram.
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
    "avisos_programados_activos": 0,
    "ultimo_error": None,
}

# ══════════════════════════════════════════════════════════════════════════
# 2. MAPA DE ACTIVOS: DIVISAS -> ÍNDICES Y SECTORES ACCIONARIOS
# ══════════════════════════════════════════════════════════════════════════
# Conecta cada divisa con los índices bursátiles y los sectores de acciones
# que más se mueven cuando sale un dato económico de ese país.
MAPA_ACTIVOS = {
    "USD": {
        "indices": "S&P 500 (US500), Nasdaq 100 (US100) y Dow Jones (US30)",
        "acciones_sensibles": "Tecnológicas/Big Tech (AAPL, NVDA, MSFT) y Banca (JPM, BAC)",
        "nota_pais": "El dólar marca el costo global del dinero; tasas altas en EE.UU. golpean con más fuerza al Nasdaq que al Dow Jones.",
    },
    "EUR": {
        "indices": "DAX 40 (Alemania), Euro Stoxx 50 y CAC 40",
        "acciones_sensibles": "Industriales/Exportadoras alemanas (SAP, Siemens, automotrices) y Banca europea",
        "nota_pais": "Un euro demasiado caro suele restar competitividad a las exportadoras del DAX 40.",
    },
    "GBP": {
        "indices": "FTSE 100 (UK100)",
        "acciones_sensibles": "Financieras, Energía y Consumo del Reino Unido (HSBC, Shell, Unilever)",
        "nota_pais": "Muchas empresas del FTSE 100 facturan en dólares; si la libra sube fuerte, el FTSE 100 a veces se frena.",
    },
    "JPY": {
        "indices": "Nikkei 225 (JP225)",
        "acciones_sensibles": "Exportadoras japonesas (Toyota, Sony, Tokyo Electron)",
        "nota_pais": "Relación inversa clásica: cuando el Yen (JPY) se fortalece por subidas de tasas del BoJ, el Nikkei 225 suele caer porque encarece sus exportaciones.",
    },
    "AUD": {
        "indices": "ASX 200 (AUS200)",
        "acciones_sensibles": "Mineras y Materias Primas (BHP, Rio Tinto) y Banca australiana",
        "nota_pais": "Divisa muy ligada al ciclo de materias primas y al crecimiento industrial de China.",
    },
    "CAD": {
        "indices": "S&P/TSX 60 (Canadá)",
        "acciones_sensibles": "Sector Energético/Petrolero y Bancos canadienses",
        "nota_pais": "Altamente correlacionado con los precios del petróleo (WTI) y la demanda de EE.UU.",
    },
    "CHF": {
        "indices": "SMI 20 (Suiza)",
        "acciones_sensibles": "Defensivas/Farmacéuticas (Nestlé, Roche, Novartis) y Financieras (UBS)",
        "nota_pais": "Actúa como activo refugio; un franco demasiado fuerte presiona los márgenes de sus multinacionales.",
    },
    "NZD": {
        "indices": "NZX 50",
        "acciones_sensibles": "Agroindustria y Exportadoras de lácteos/alimentos",
        "nota_pais": "Sensible al apetito global por el riesgo y al comercio en Asia-Pacífico.",
    },
}


# ══════════════════════════════════════════════════════════════════════════
# 3. PERSISTENCIA EN DISCO
# ══════════════════════════════════════════════════════════════════════════
_ESTADO_DEFECTO = {"programados": [], "reacciones_enviadas": []}
_lock_archivo = threading.Lock()


def _cargar_estado_disco():
    with _lock_archivo:
        if not os.path.exists(ARCHIVO_ESTADO):
            return dict(_ESTADO_DEFECTO)
        try:
            with open(ARCHIVO_ESTADO, "r", encoding="utf-8") as f:
                estado = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("No se pudo leer %s (%s); se empieza de cero.", ARCHIVO_ESTADO, e)
            return dict(_ESTADO_DEFECTO)
        estado.setdefault("programados", [])
        estado.setdefault("reacciones_enviadas", [])
        return estado


def _guardar_estado_disco(estado):
    with _lock_archivo:
        try:
            with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
                json.dump(estado, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log.warning("No se pudo guardar %s: %s", ARCHIVO_ESTADO, e)


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


# ══════════════════════════════════════════════════════════════════════════
# 4. SERVIDOR WEB KEEP-ALIVE (FLASK)
# ══════════════════════════════════════════════════════════════════════════
@app.route("/")
def keep_alive():
    return (
        "Bot de Análisis Fundamental Multi-Activo activo (America/Bogota).\n"
        f"Arrancó: {_estado_salud['arranque']}\n"
        f"Última sincronización: {_estado_salud['ultima_sincronizacion']}\n"
        f"Eventos relevantes hoy (Medio y Alto impacto): {_estado_salud['eventos_detectados_hoy']}\n"
        f"Trabajos activos en el programador: {_estado_salud['avisos_programados_activos']}\n"
        f"Último error: {_estado_salud['ultimo_error']}\n"
    )


def run_flask():
    puerto = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=puerto)


# ══════════════════════════════════════════════════════════════════════════
# 5. DESCARGA DEL CALENDARIO ECONÓMICO (Investing.com)
# ══════════════════════════════════════════════════════════════════════════
_CACHE_CALENDARIO = {"eventos": None, "momento": None}
_CACHE_TTL_SEGUNDOS = 120
_lock_cache_calendario = threading.Lock()

_HEADERS_INVESTING = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
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
    titulo_crudo = (
        enlace.get_text() if enlace else (celda_evento.get_text() if celda_evento else "")
    )
    titulo = re.sub(r"\s*\([^)]*\)\s*$", "", titulo_crudo).strip()

    def _texto_celda(sufijo):
        celda = fila.find("td", id=re.compile(rf"^event{sufijo}_\d+$"))
        return celda.get_text(strip=True) if celda else ""

    return {
        "titulo": titulo,
        "divisa": divisa,
        "impacto": impacto,
        "fecha": fecha,
        "pronostico": _texto_celda("Forecast"),
        "anterior": _texto_celda("Previous"),
        "actual": _texto_celda("Actual"),
    }


def obtener_calendario():
    with _lock_cache_calendario:
        momento_cache = _CACHE_CALENDARIO["momento"]
        if momento_cache is not None:
            edad = (datetime.now(pytz.utc) - momento_cache).total_seconds()
            if edad < _CACHE_TTL_SEGUNDOS:
                return _CACHE_CALENDARIO["eventos"]

        cuerpo = (
            "importance%5B%5D=2&importance%5B%5D=3"
            "&timeZone=8&timeFilter=timeRemain&currentTab=thisWeek"
            + "".join(f"&country%5B%5D={cid}" for cid in _PAISES_OBJETIVO_IDS)
        )
        try:
            peticion = urllib.request.Request(
                URL_CALENDARIO,
                data=cuerpo.encode("utf-8"),
                headers=_HEADERS_INVESTING,
                method="POST",
            )
            with urllib.request.urlopen(peticion, timeout=20) as respuesta:
                cuerpo_respuesta = respuesta.read().decode("utf-8", errors="ignore")
            html = json.loads(cuerpo_respuesta).get("data", "")
        except (urllib.error.URLError, ValueError) as e:
            log.error("No se pudo descargar el calendario de Investing.com: %s", e)
            _estado_salud["ultimo_error"] = f"calendario: {e}"
            return []

        eventos = []
        soup = BeautifulSoup(html, "html.parser")
        for fila in soup.find_all("tr", class_="js-event-item"):
            try:
                evento = _parsear_fila_evento(fila)
            except Exception as e:
                log.warning("No se pudo leer una fila del calendario: %s", e)
                continue
            if evento is not None:
                eventos.append(evento)

        _CACHE_CALENDARIO["eventos"] = eventos
        _CACHE_CALENDARIO["momento"] = datetime.now(pytz.utc)
        return eventos


# ══════════════════════════════════════════════════════════════════════════
# 6. MOTOR DE ANÁLISIS FUNDAMENTAL, MULTI-ACTIVO Y DESCUENTO DE MERCADO
# ══════════════════════════════════════════════════════════════════════════
# Además de 'alcista_si_sube' para la divisa, cada categoría define cómo
# reacciona la BOLSA (índices y acciones) cuando el dato supera expectativas:
#   bolsa_si_sube:
#     "alcista" -> Dato alto impulsa bolsa (ej. crecimiento PIB/PMI sano)
#     "bajista" -> Dato alto golpea bolsa por temor a tasas altas (ej. IPC, tasas)
#     "mixto"   -> Dato bueno para economía real pero frena recortes de tasas (ej. NFP)
CATEGORIAS = {
    "tasas": {
        "claves": [
            "tasa de interés", "tasas de interés", "interest rate",
            "tipo de interés", "fomc statement", "boe rate", "ecb rate",
            "rba rate", "boc rate", "snb rate", "rate decision",
            "decisión de tasas", "decisión de tipos",
            "federal funds rate", "official bank rate", "overnight rate",
            "cash rate", "economic projections", "summary of economic projections",
            "dot plot", "policy rate", "bank rate", "main refinancing rate",
            "deposit facility rate", "repo rate", "base rate",
        ],
        "nombre": "Decisión de tasas de interés",
        "nivel": "clave",
        "alcista_si_sube": True,
        "bolsa_si_sube": "bajista",
        "razon_alcista": (
            "tasas más altas (o una pausa restrictiva) pagan mayor rendimiento a los "
            "bonos de ese país, atrayendo flujo de capital hacia la divisa"
        ),
        "razon_bajista": (
            "un recorte de tasas (o postura más flexible) abarata el dinero y reduce "
            "el rendimiento de la moneda frente a otras divisas"
        ),
        "efecto_bolsa_sube": (
            "BAJISTA para índices y acciones de crecimiento/tecnológicas (el crédito caro "
            "reduce múltiplos de valoración), aunque suele apoyar a acciones bancarias"
        ),
        "efecto_bolsa_baja": (
            "ALCISTA para índices bursátiles y acciones (especialmente tecnológicas, "
            "inmobiliarias y consumo) porque abarata el financiamiento empresarial"
        ),
    },
    "ipc": {
        "claves": [
            "ipc", "cpi", "inflación", "pce", "índice de precios",
            "precios al consumidor", "ppi", "producer price",
            "índice de precios al productor", "wage price", "average hourly earnings",
        ],
        "nombre": "Inflación y Precios (IPC / PCE / IPP / Salarios)",
        "nivel": "clave",
        "alcista_si_sube": True,
        "bolsa_si_sube": "bajista",
        "razon_alcista": (
            "una inflación más caliente obliga al banco central a mantener tasas altas "
            "por más tiempo (postura Hawkish), fortaleciendo la divisa"
        ),
        "razon_bajista": (
            "una inflación más baja acerca los recortes de tasas de interés (postura "
            "Dovish), restando fuerza a la divisa"
        ),
        "efecto_bolsa_sube": (
            "BAJISTA para índices y acciones (sobre todo Nasdaq / Tech): el temor a "
            "tasas altas sube los rendimientos de los bonos y presiona a la bolsa a la baja"
        ),
        "efecto_bolsa_baja": (
            "ALCISTA para índices y acciones: la desinflación da luz verde al banco "
            "central para bajar tasas, impulsando el apetito por riesgo en bolsa"
        ),
    },
    "pib": {
        "claves": ["pib", "gdp", "producto interno bruto"],
        "nombre": "Producto Interno Bruto (PIB)",
        "nivel": "clave",
        "alcista_si_sube": True,
        "bolsa_si_sube": "alcista",
        "razon_alcista": (
            "una economía que crece más rápido atrae inversión extranjera y da margen "
            "al banco central para no apurar recortes de tasas"
        ),
        "razon_bajista": (
            "un crecimiento estancado o menor al previsto aviva temores de desaceleración "
            "y presiona al banco central a recortar tasas"
        ),
        "efecto_bolsa_sube": (
            "ALCISTA para índices y acciones cíclicas (bancos, industria, consumo) porque "
            "refleja mayores ventas y utilidades empresariales"
        ),
        "efecto_bolsa_baja": (
            "BAJISTA para acciones cíclicas e índices por temor a menores ganancias "
            "corporativas (favorece rotación hacia sectores defensivos como salud o servicios)"
        ),
    },
    "empleo": {
        "claves": [
            "nóminas", "nfp", "payroll", "nonfarm", "non-farm",
            "cambio de empleo", "creación de empleo", "empleo no agrícola",
            "employment change", "adp", "jolts",
        ],
        "nombre": "Creación de Empleo (Nóminas / NFP / ADP)",
        "nivel": "clave",
        "alcista_si_sube": True,
        "bolsa_si_sube": "mixto",
        "razon_alcista": (
            "más creación de empleo demuestra fortaleza económica y aleja la urgencia "
            "de bajar tasas de interés, impulsando la divisa"
        ),
        "razon_bajista": (
            "menos empleos creados señala enfriamiento en el mercado laboral y aumenta "
            "la probabilidad de recortes de tasas, debilitando la divisa"
        ),
        "efecto_bolsa_sube": (
            "MIXTO / VOLÁTIL para índices: un empleo sólido apoya el consumo (positivo "
            "para empresas), pero si sale excesivamente alto asusta a la bolsa porque "
            "retrasa los recortes de tasas"
        ),
        "efecto_bolsa_baja": (
            "MIXTO: una caída moderada suele subir a los índices (esperanza de tasas más "
            "bajas), pero un desplome severo tira la bolsa por miedo a recesión"
        ),
    },
    "desempleo_tasa": {
        "claves": ["tasa de desempleo", "unemployment rate"],
        "nombre": "Tasa de desempleo",
        "nivel": "clave",
        "alcista_si_sube": False,  # Indicador inverso: menos desempleo = alcista divisa
        "bolsa_si_sube": "mixto",
        "razon_alcista": (
            "un desempleo más bajo de lo esperado confirma estrechez laboral y presiona "
            "salarios al alza, sosteniendo tasas altas y fortaleciendo la divisa"
        ),
        "razon_bajista": (
            "un aumento del desempleo es señal directa de deterioro económico, obligando "
            "al banco central a proteger el empleo mediante recortes de tasas"
        ),
        "efecto_bolsa_sube": (
            "PRECAUCIÓN en bolsa: si el desempleo sube ligeramente, el mercado a veces "
            "celebra que habrá recortes de tasas; si sube fuerte, las acciones caen por "
            "temor a caída del consumo"
        ),
        "efecto_bolsa_baja": (
            "SOLIDEZ macroeconómica, pero si el desempleo baja demasiado reduce el "
            "margen para bajar tasas, frenando a las acciones tecnológicas"
        ),
    },
    "solicitudes_desempleo": {
        "claves": [
            "solicitudes de desempleo", "solicitudes por desempleo",
            "jobless claims", "unemployment claims",
            "subsidio por desempleo", "claimant count",
        ],
        "nombre": "Solicitudes semanales de subsidio por desempleo",
        "nivel": "secundario",
        "alcista_si_sube": False,  # Indicador inverso
        "bolsa_si_sube": "mixto",
        "razon_alcista": (
            "menos despidos de los esperados muestra que las empresas siguen reteniendo "
            "personal, respaldando a la divisa"
        ),
        "razon_bajista": (
            "más solicitudes de subsidio indican aumento de despidos y enfriamiento "
            "económico, pesando a la baja sobre la divisa"
        ),
        "efecto_bolsa_sube": (
            "Suele aliviar los rendimientos de los bonos y dar respiro intradía a las "
            "acciones tecnológicas, salvo que la cifra sea tan alta que sugiera recesión"
        ),
        "efecto_bolsa_baja": (
            "Favorece a sectores cíclicos y financieros por solidez laboral, pero presiona "
            "a la baja a sectores sensibles a tasas de interés"
        ),
    },
    "pmi": {
        "claves": [
            "pmi", "ism", "índice de gerentes de compra",
            "actividad manufacturera", "actividad de servicios",
            "hcob", "s&p global", "empire state", "philly fed", "philadelphia fed",
        ],
        "nombre": "PMI / ISM (Actividad Manufacturera y Servicios)",
        "nivel": "clave",
        "alcista_si_sube": True,
        "bolsa_si_sube": "alcista",
        "razon_alcista": (
            "un PMI superior a lo esperado (especialmente sobre el nivel de 50.0) indica "
            "expansión económica adelantada, fortaleciendo la divisa"
        ),
        "razon_bajista": (
            "un PMI inferior a lo esperado (o bajo 50.0) señala contracción en pedidos "
            "y actividad empresarial, debilitando la divisa"
        ),
        "efecto_bolsa_sube": (
            "ALCISTA para índices e industriales (más pedidos y actividad = mejores "
            "ingresos corporativos), salvo que venga acompañado de precios altos"
        ),
        "efecto_bolsa_baja": (
            "BAJISTA para índices y acciones cíclicas/industriales por señal temprana "
            "de desaceleración en la economía real"
        ),
    },
    "ventas_minoristas": {
        "claves": ["ventas minoristas", "retail sales", "gasto del consumidor", "personal spending"],
        "nombre": "Ventas Minoristas y Consumo",
        "nivel": "secundario",
        "alcista_si_sube": True,
        "bolsa_si_sube": "alcista",
        "razon_alcista": (
            "un consumidor gastando con fuerza impulsa el PIB y reduce la necesidad de "
            "estímulos monetarios, apoyando a la divisa"
        ),
        "razon_bajista": (
            "una caída en las ventas muestra que los hogares están frenando el gasto, "
            "lo que debilita las perspectivas de crecimiento y la divisa"
        ),
        "efecto_bolsa_sube": (
            "ALCISTA para acciones de consumo discrecional, retail y comercio electrónico"
        ),
        "efecto_bolsa_baja": (
            "BAJISTA para acciones de comercio, consumo y banca de consumo"
        ),
    },
    "confianza_consumidor": {
        "claves": [
            "confianza del consumidor", "consumer confidence",
            "sentimiento del consumidor", "consumer sentiment",
            "zew", "ifo", "confianza empresarial", "business confidence",
        ],
        "nombre": "Confianza del Consumidor / Sentimiento Económico",
        "nivel": "secundario",
        "alcista_si_sube": True,
        "bolsa_si_sube": "alcista",
        "razon_alcista": (
            "mayor optimismo de hogares y empresas anticipa más consumo e inversión en "
            "los próximos meses, favoreciendo a la divisa"
        ),
        "razon_bajista": (
            "el pesimismo anticipa recorte de gastos e inversión futura, presionando "
            "a la baja a la divisa"
        ),
        "efecto_bolsa_sube": "ALCISTA moderado para índices bursátiles y sector consumo",
        "efecto_bolsa_baja": "BAJISTA moderado para índices por cautela sobre la demanda futura",
    },
    "balanza_comercial": {
        "claves": ["balanza comercial", "trade balance", "cuenta corriente", "current account"],
        "nombre": "Balanza Comercial / Cuenta Corriente",
        "nivel": "secundario",
        "alcista_si_sube": True,
        "bolsa_si_sube": "alcista",
        "razon_alcista": "mayor superávit (o menor déficit) implica mayor demanda neta de la moneda local por exportaciones",
        "razon_bajista": "un déficit comercial mayor implica salida neta de divisas para pagar importaciones",
        "efecto_bolsa_sube": "Positivo para acciones exportadoras e industriales del país",
        "efecto_bolsa_baja": "Debilidad en el sector exportador e industrial",
    },
    "vivienda": {
        "claves": [
            "vivienda", "housing", "permisos de construcción",
            "building permits", "housing starts", "inicios de construcción",
            "home sales", "ventas de viviendas",
        ],
        "nombre": "Sector Vivienda e Inmobiliario",
        "nivel": "secundario",
        "alcista_si_sube": True,
        "bolsa_si_sube": "alcista",
        "razon_alcista": "muestra que el sector inmobiliario resiste las tasas hipotecarias actuales, apoyando a la divisa",
        "razon_bajista": "confirma que las tasas hipotecarias altas están enfriando la construcción y el crédito",
        "efecto_bolsa_sube": "ALCISTA para constructoras, inmobiliarias (REITs) y materiales",
        "efecto_bolsa_baja": "BAJISTA para el sector inmobiliario y construcción",
    },
    "discurso": {
        "claves": [
            "discurso", "habla", "speech", "speaks", "speak", "remarks",
            "testimonio", "testimony", "conferencia de prensa",
            "press conference", "comparece",
        ],
        "nombre": "Discurso o comparecencia de un banquero central",
        "nivel": "clave",
        "alcista_si_sube": None,
        "bolsa_si_sube": None,
        "razon_alcista": None,
        "razon_bajista": None,
    },
    "actas": {
        "claves": ["actas", "minutes", "fomc minutes", "monetary policy report"],
        "nombre": "Actas o Informe de Política Monetaria",
        "nivel": "clave",
        "alcista_si_sube": None,
        "bolsa_si_sube": None,
        "razon_alcista": None,
        "razon_bajista": None,
    },
}

CATEGORIA_DEFECTO = {
    "nombre": "Indicador macroeconómico",
    "nivel": "descartar",
    "alcista_si_sube": None,
    "bolsa_si_sube": None,
    "razon_alcista": None,
    "razon_bajista": None,
}

_PATRON_PROYECCION_SUELTA = re.compile(
    r"(interest rate|rate) projection.*\b(year|qtr|quarter)\b", re.IGNORECASE
)


def clasificar_evento(titulo):
    if _PATRON_PROYECCION_SUELTA.search(titulo or ""):
        return CATEGORIA_DEFECTO
    titulo_normalizado = (titulo or "").lower()
    for datos in CATEGORIAS.values():
        if any(clave in titulo_normalizado for clave in datos["claves"]):
            return datos
    return CATEGORIA_DEFECTO


def _nivel_de_relevancia(titulo):
    return clasificar_evento(titulo).get("nivel", "descartar")


_PALABRAS_JEFE_BANCO_CENTRAL = ["chair", "president", "governor", " gov ", "chairman"]
_PALABRAS_NO_ES_EL_JEFE = ["assist", "deputy", "vice", "member"]


def _es_jefe_banco_central(titulo, divisa):
    t = f" {(titulo or '').lower()} "
    if any(p in t for p in _PALABRAS_NO_ES_EL_JEFE):
        return False
    if not any(p in t for p in _PALABRAS_JEFE_BANCO_CENTRAL):
        return False
    if divisa == "EUR" and "ecb" not in t and "bce" not in t:
        return False
    return True


def eventos_relevantes(eventos, solo_hoy=False):
    ahora_bogota = datetime.now(ZONA_HORARIA)
    resultado = []
    for ev in eventos:
        if ev["divisa"] not in DIVISAS_OBJETIVO:
            continue
        nivel = _nivel_de_relevancia(ev["titulo"])
        if nivel == "descartar":
            continue
        if nivel == "secundario" and not INCLUIR_SECUNDARIOS:
            continue

        categoria = clasificar_evento(ev["titulo"])
        es_discurso = categoria.get("nombre") == "Discurso o comparecencia de un banquero central"
        es_jefe_hablando = es_discurso and _es_jefe_banco_central(ev["titulo"], ev["divisa"])

        # Si es un discurso de un miembro menor (no jefe) en impacto medio,
        # se filtra para no llenarte de ruido; si es alto impacto o el jefe, pasa.
        if es_discurso and ev["impacto"] != "High" and not es_jefe_hablando:
            continue

        if ev["impacto"] not in IMPACTOS_A_MONITOREAR and not es_jefe_hablando:
            continue

        if solo_hoy:
            fecha_bogota = ev["fecha"].astimezone(ZONA_HORARIA)
            if fecha_bogota.date() != ahora_bogota.date():
                continue
        resultado.append(ev)
    return resultado


def _a_numero(texto):
    """
    CORREGIDO: Convierte '3.2%', '170K', '1.05M', '-0.4B', '1,234' a un float
    aplicando el multiplicador real del sufijo para que comparar '980K' contra
    '1.02M' sea matemáticamente exacto.
    """
    if not texto:
        return None
    limpio = texto.strip().replace("%", "").replace(",", "").replace("$", "").strip()
    if not limpio:
        return None

    multiplicador = 1.0
    ultimo_char = limpio[-1].upper()
    if ultimo_char == "K":
        multiplicador = 1_000.0
        limpio = limpio[:-1]
    elif ultimo_char == "M":
        multiplicador = 1_000_000.0
        limpio = limpio[:-1]
    elif ultimo_char == "B":
        multiplicador = 1_000_000_000.0
        limpio = limpio[:-1]
    elif ultimo_char == "T":
        multiplicador = 1_000_000_000_000.0
        limpio = limpio[:-1]

    try:
        return float(limpio) * multiplicador
    except ValueError:
        return None


def evaluar_descuento_previo(evento):
    """
    Analiza ANTES de que salga la noticia si el mercado ya viene descontando
    (pricing-in) un resultado alcista o bajista, comparando el Pronóstico
    del consenso contra el Dato Anterior.
    """
    categoria = clasificar_evento(evento.get("titulo", ""))
    alcista_si_sube = categoria.get("alcista_si_sube")
    if alcista_si_sube is None:
        return None

    pron = _a_numero(evento.get("pronostico"))
    ant = _a_numero(evento.get("anterior"))
    if pron is None or ant is None:
        return None

    diff = pron - ant
    escala = max(abs(ant), abs(pron), 1e-6)
    cambio_relativo = abs(diff) / escala

    if cambio_relativo < 0.015:
        return (
            "⚖️ *Descuento previo (Priced-in):* El consenso espera un dato casi idéntico "
            f"al anterior ({evento['pronostico']} vs {evento['anterior']}). No hay una "
            "inclinación fuerte descontada; cualquier desviación moverá el precio con limpieza."
        )

    espera_subida_num = diff > 0
    mercado_descuenta_alcista_divisa = espera_subida_num if alcista_si_sube else not espera_subida_num

    if mercado_descuenta_alcista_divisa:
        return (
            "📉 *Descuento previo (Priced-in):* El consenso ya espera un dato FAVORABLE "
            f"para {evento['divisa']} ({evento['pronostico']} vs {evento['anterior']} anterior). "
            "Gran parte de este optimismo **ya puede estar descontado en el precio**. "
            "Si el dato sale solo en línea con el pronóstico, cuidado con toma de ganancias "
            "('comprar el rumor, vender la noticia'); para que suba con fuerza necesita "
            "superar el pronóstico."
        )
    else:
        return (
            "📈 *Descuento previo (Priced-in):* El consenso ya espera un dato DÉBIL "
            f"para {evento['divisa']} ({evento['pronostico']} vs {evento['anterior']} anterior). "
            "El pesimismo **ya viene descontándose**. Si el dato sale apenas como se espera, "
            "la caída puede frenarse rápido; pero si sorprende saliendo mejor que el pronóstico, "
            "puede provocar un rebote alcista fuerte (cierre de cortos)."
        )


def analizar_resultado(evento):
    """
    Analiza el dato PUBLICADO (Actual) frente al Pronóstico y al Anterior.
    Determina:
      1. Dirección para la Divisa
      2. Dirección para Índices y Acciones
      3. Si el movimiento ya estaba descontado (sin sorpresa real) o si hubo
         sorpresa/giro contra el consenso.
    """
    categoria = clasificar_evento(evento.get("titulo", ""))
    if categoria.get("alcista_si_sube") is None:
        return None

    actual = _a_numero(evento.get("actual"))
    pronostico = _a_numero(evento.get("pronostico"))
    if actual is None or pronostico is None:
        return None

    anterior = _a_numero(evento.get("anterior"))
    diferencia = actual - pronostico
    escala = max(abs(pronostico), abs(actual), 1e-6)
    sorpresa_relativa = abs(diferencia) / escala

    divisa = evento.get("divisa", "")
    info_activos = MAPA_ACTIVOS.get(divisa, {})
    indices_txt = info_activos.get("indices", f"Índices de {divisa}")

    # Caso 1: Salió exactamente igual o con desviación minúscula (< 1%)
    if abs(diferencia) < 1e-9 or sorpresa_relativa < 0.008:
        lectura_descuento = (
            "Dato **YA DESCONTADO (Priced-in)**: salió prácticamente clavado al pronóstico. "
            "Cuando no hay sorpresa, el impacto suele limitarse a un barrido de liquidez "
            "inicial (mechas arriba y abajo) o una toma de ganancias del movimiento previo."
        )
        return {
            "resultado_txt": "salió en línea con lo esperado (sin sorpresa)",
            "direccion": "neutral",
            "razon_divisa": "sin sorpresa frente al consenso, no genera impulso direccional nuevo",
            "impacto_bolsa": f"NEUTRAL para {indices_txt} (movimiento ya descontado)",
            "descuento_txt": lectura_descuento,
            "confirmacion": None,
        }

    salio_por_encima = diferencia > 0
    es_alcista_divisa = salio_por_encima if categoria["alcista_si_sube"] else not salio_por_encima
    razon_divisa = categoria["razon_alcista"] if es_alcista_divisa else categoria["razon_bajista"]

    # Impacto en Bolsa (Índices y Acciones)
    efecto_bolsa = (
        categoria.get("efecto_bolsa_sube") if salio_por_encima
        else categoria.get("efecto_bolsa_baja")
    ) or "Impacto mixto según el apetito por riesgo de la sesión"

    # Evaluación de Descuento vs Sorpresa y Comparación con el Anterior
    confirmacion = None
    descuento_txt = ""
    es_sorpresa_fuerte = sorpresa_relativa >= 0.05

    if anterior is not None and abs(pronostico - anterior) > 1e-9:
        # Qué descontaba el mercado antes del dato
        esperaba_mejora = (
            (pronostico > anterior) if categoria["alcista_si_sube"] else (pronostico < anterior)
        )
        mejora_vs_anterior = (
            (actual > anterior) if categoria["alcista_si_sube"] else (actual < anterior)
        )

        if mejora_vs_anterior == es_alcista_divisa:
            confirmacion = "Doble confirmación: supera tanto al pronóstico como al dato anterior"
        else:
            confirmacion = "Lectura mixta: supera al pronóstico, pero sigue peor que el dato anterior"

        if esperaba_mejora != es_alcista_divisa:
            descuento_txt = (
                "⚡ *CHOQUE CONTRA EL DESCUENTO PREVIO:* El mercado venía descontando lo "
                "contrario en el pronóstico. Esta sorpresa suele forzar cierres de posiciones "
                "atrapadas y generar un movimiento direccional más prolongado."
            )
        elif es_sorpresa_fuerte:
            descuento_txt = (
                "🚀 *SORPRESA NO DESCONTADA:* Aunque iba en la dirección esperada, la magnitud "
                "supera ampliamente lo que el mercado tenía en precio. Probable continuación "
                "fuerte tras la publicación."
            )
        else:
            descuento_txt = (
                "⚠️ *SORPRESA MODERADA:* Va en la misma dirección que el mercado ya descontaba "
                "en el pronóstico. Espera reacción inicial a favor, pero vigila si se agota "
                "rápido en resistencias/soportes cercanos."
            )
    else:
        descuento_txt = (
            "🚀 *SORPRESA FUERTE NO DESCONTADA:* Desviación amplia frente al consenso."
            if es_sorpresa_fuerte
            else "📊 *SORPRESA MODERADA:* Desviación moderada respecto al pronóstico."
        )

    return {
        "resultado_txt": (
            "salió por ENCIMA de lo esperado" if salio_por_encima
            else "salió por DEBAJO de lo esperado"
        ),
        "direccion": "alcista" if es_alcista_divisa else "bajista",
        "razon_divisa": razon_divisa,
        "impacto_bolsa": f"{efecto_bolsa} ({indices_txt})",
        "descuento_txt": descuento_txt,
        "confirmacion": confirmacion,
    }


# ══════════════════════════════════════════════════════════════════════════
# 7. TRADUCCIÓN Y CONSTRUCCIÓN DE MENSAJES EN ESPAÑOL
# ══════════════════════════════════════════════════════════════════════════
_IMPACTO_ES = {
    "High": "Alto 🔴 (3 toros)",
    "Medium": "Medio 🟠 (2 toros)",
    "Low": "Bajo 🟡 (1 toro)",
    "Holiday": "Festivo",
}


def traducir_impacto(impacto):
    return _IMPACTO_ES.get(impacto, impacto)


_TRADUCCIONES_TITULO = [
    (r"\bADP Non-Farm Employment Change\b", "Cambio de Empleo ADP (sector privado)"),
    (r"\bNon-Farm Employment Change\b", "Cambio de Empleo No Agrícola"),
    (r"\bNon-Farm Payrolls\b", "Nóminas No Agrícolas (NFP)"),
    (r"\bFull Employment Change\b", "Cambio en el Empleo a Tiempo Completo"),
    (r"\bPart[- ]?Time Employment Change\b", "Cambio en el Empleo a Tiempo Parcial"),
    (r"\bEmployment Change\b", "Cambio en el Empleo"),
    (r"\bAverage Hourly Earnings\b", "Ingreso Medio por Hora (Salarios)"),
    (r"\bJOLTS Job Openings\b", "Ofertas de Empleo JOLTS"),
    (r"\bUnemployment Claims\b", "Solicitudes de Subsidio por Desempleo"),
    (r"\bInitial Jobless Claims\b", "Solicitudes Iniciales de Subsidio por Desempleo"),
    (r"\bContinuing Jobless Claims\b", "Solicitudes Continuas de Subsidio por Desempleo"),
    (r"\bClaimant Count Change\b", "Cambio en Solicitudes de Desempleo"),
    (r"\bUnemployment Rate\b", "Tasa de Desempleo"),
    (r"\bCore PCE Price Index\b", "Índice de Precios PCE Subyacente"),
    (r"\bPCE Price Index\b", "Índice de Precios PCE"),
    (r"\bMedian CPI\b", "IPC Mediano"),
    (r"\bTrimmed CPI\b", "IPC Recortado"),
    (r"\bCore CPI\b", "IPC Subyacente"),
    (r"\bCPI\b", "IPC"),
    (r"\bCore PPI\b", "IPP Subyacente"),
    (r"\bProducer Price Index\b", "Índice de Precios al Productor (IPP)"),
    (r"\bPPI\b", "IPP"),
    (r"\bPrelim GDP\b", "PIB Preliminar"),
    (r"\bFinal GDP\b", "PIB Final"),
    (r"\bGDP\b", "PIB"),
    (r"\bCore Retail Sales\b", "Ventas Minoristas Subyacentes"),
    (r"\bRetail Sales\b", "Ventas Minoristas"),
    (r"\bTrade Balance\b", "Balanza Comercial"),
    (r"\bCurrent Account\b", "Cuenta Corriente"),
    (r"\bCB Consumer Confidence\b", "Confianza del Consumidor (Conference Board)"),
    (r"\bConsumer Confidence\b", "Confianza del Consumidor"),
    (r"\bMichigan Consumer Sentiment\b", "Sentimiento del Consumidor (Michigan)"),
    (r"\bConsumer Sentiment\b", "Sentimiento del Consumidor"),
    (r"\bBusiness Confidence\b", "Confianza Empresarial"),
    (r"\bZEW Economic Sentiment\b", "Sentimiento Económico ZEW"),
    (r"\bIfo Business Climate\b", "Clima Empresarial Ifo"),
    (r"\bEconomic Sentiment\b", "Sentimiento Económico"),
    (r"\bBuilding Permits\b", "Permisos de Construcción"),
    (r"\bHousing Starts\b", "Inicios de Construcción de Viviendas"),
    (r"\bExisting Home Sales\b", "Ventas de Viviendas Existentes"),
    (r"\bNew Home Sales\b", "Ventas de Viviendas Nuevas"),
    (r"\bPending Home Sales\b", "Ventas de Viviendas Pendientes"),
    (r"\bHCOB Germany Composite PMI\b", "PMI Compuesto de Alemania (HCOB)"),
    (r"\bHCOB Germany Manufacturing PMI\b", "PMI Manufacturero de Alemania (HCOB)"),
    (r"\bHCOB Germany Services PMI\b", "PMI de Servicios de Alemania (HCOB)"),
    (r"\bHCOB Eurozone Composite PMI\b", "PMI Compuesto de la Eurozona (HCOB)"),
    (r"\bHCOB Eurozone Manufacturing PMI\b", "PMI Manufacturero de la Eurozona (HCOB)"),
    (r"\bHCOB Eurozone Services PMI\b", "PMI de Servicios de la Eurozona (HCOB)"),
    (r"\bManufacturing (?:&|and) Services PMI\b", "PMI Manufacturero y de Servicios"),
    (r"\bS&P Global Manufacturing PMI\b", "PMI Manufacturero (S&P Global)"),
    (r"\bS&P Global Services PMI\b", "PMI de Servicios (S&P Global)"),
    (r"\bS&P Global Composite PMI\b", "PMI Compuesto (S&P Global)"),
    (r"\bISM Manufacturing PMI\b", "PMI Manufacturero ISM"),
    (r"\bISM Services PMI\b", "PMI de Servicios ISM"),
    (r"\bManufacturing PMI\b", "PMI Manufacturero"),
    (r"\bServices PMI\b", "PMI de Servicios"),
    (r"\bComposite PMI\b", "PMI Compuesto"),
    (r"\bEmpire State Manufacturing Index\b", "Índice Manufacturero Empire State"),
    (r"\bPhiladelphia Fed Manufacturing Index\b", "Índice Manufacturero de la Fed de Filadelfia"),
    (r"\bInterest Rate Decision\b", "Decisión de Tipos de Interés"),
    (r"\bFederal Funds Rate\b", "Tasa de Fondos Federales (Fed)"),
    (r"\bOfficial Bank Rate\b", "Tasa Oficial del Banco de Inglaterra"),
    (r"\bOvernight Rate\b", "Tasa Overnight"),
    (r"\bCash Rate\b", "Tasa de Efectivo"),
    (r"\bSNB Policy Rate\b", "Tasa de Política del SNB"),
    (r"\bPolicy Rate\b", "Tasa de Política Monetaria"),
    (r"\bBank Rate\b", "Tasa Bancaria"),
    (r"\bMain Refinancing Rate\b", "Tasa de Refinanciación Principal (BCE)"),
    (r"\bDeposit Facility Rate\b", "Tasa de Facilidad de Depósito (BCE)"),
    (r"\bFOMC Statement\b", "Comunicado del FOMC"),
    (r"\bFOMC Meeting Minutes\b", "Actas de la Reunión del FOMC"),
    (r"\bFOMC Press Conference\b", "Rueda de Prensa del FOMC"),
    (r"\bMonetary Policy Statement\b", "Comunicado de Política Monetaria"),
    (r"\bMonetary Policy Report\b", "Informe de Política Monetaria"),
    (r"\bMonetary Policy Assessment\b", "Evaluación de Política Monetaria"),
    (r"\bRate Statement\b", "Comunicado de Tasas"),
    (r"\bPress Conference\b", "Rueda de Prensa"),
    (r"\bSpeaks\b", "habla"),
    (r"\bSpeech\b", "Discurso"),
    (r"\bTestimony\b", "Comparecencia"),
    (r"\bIndustrial Production\b", "Producción Industrial"),
    (r"\bFactory Orders\b", "Pedidos de Fábrica"),
    (r"\bCore Durable Goods Orders\b", "Pedidos de Bienes Duraderos Subyacentes"),
    (r"\bDurable Goods Orders\b", "Pedidos de Bienes Duraderos"),
    (r"\bWholesale Inventories\b", "Inventarios Mayoristas"),
    (r"\bGerman\b", "Alemán"),
    (r"\bFrench\b", "Francés"),
    (r"\bItalian\b", "Italiano"),
    (r"\bSpanish\b", "Español"),
    (r"\bChinese\b", "Chino"),
    (r"\bAustralian\b", "Australiano"),
    (r"\bCanadian\b", "Canadiense"),
    (r"\bBritish\b", "Británico"),
    (r"\bSwiss\b", "Suizo"),
    (r"\bJapanese\b", "Japonés"),
]
_TRADUCCIONES_TITULO_COMPILADAS = [
    (re.compile(patron, re.IGNORECASE), reemplazo)
    for patron, reemplazo in _TRADUCCIONES_TITULO
]


def traducir_titulo(titulo):
    resultado = titulo or ""
    for patron, reemplazo in _TRADUCCIONES_TITULO_COMPILADAS:
        resultado = patron.sub(reemplazo, resultado)
    return resultado


def _escapar(texto):
    return (texto or "").replace("_", "-").replace("*", "").replace("`", "'")


_BANDERAS = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵",
    "AUD": "🇦🇺", "CAD": "🇨🇦", "CHF": "🇨🇭", "NZD": "🇳🇿",
}

_EMOJI_CATEGORIA = {
    "Decisión de tasas de interés": "💰",
    "Producto Interno Bruto (PIB)": "📊",
    "Inflación y Precios (IPC / PCE / IPP / Salarios)": "🔥",
    "Tasa de desempleo": "📉",
    "Solicitudes semanales de subsidio por desempleo": "📝",
    "Creación de Empleo (Nóminas / NFP / ADP)": "👷",
    "PMI / ISM (Actividad Manufacturera y Servicios)": "🏭",
    "Ventas Minoristas y Consumo": "🛒",
    "Balanza Comercial / Cuenta Corriente": "🚢",
    "Confianza del Consumidor / Sentimiento Económico": "😊",
    "Sector Vivienda e Inmobiliario": "🏠",
    "Discurso o comparecencia de un banquero central": "🎤",
    "Actas o Informe de Política Monetaria": "📜",
}


def _bandera(divisa):
    return _BANDERAS.get(divisa, "")


def _emoji_categoria(titulo):
    nombre = clasificar_evento(titulo).get("nombre", "")
    return _EMOJI_CATEGORIA.get(nombre, "📰")


def mensaje_previo(grupo, minutos):
    divisa = grupo[0]["divisa"]
    bandera = _bandera(divisa)
    hora_local = grupo[0]["fecha"].astimezone(ZONA_HORARIA).strftime("%H:%M")
    es_urgente = minutos <= min(AVISOS_PREVIOS_MIN)
    palabra_min = "minuto" if minutos == 1 else "minutos"
    emoji = "🚨" if es_urgente else "⚠️"
    info_activos = MAPA_ACTIVOS.get(divisa, {})
    indices_afectados = info_activos.get("indices", f"Índices de {divisa}")
    acciones_afectadas = info_activos.get("acciones_sensibles", "Acciones principales")

    if len(grupo) == 1:
        evento = grupo[0]
        titulo = _escapar(traducir_titulo(evento["titulo"]))
        icono = _emoji_categoria(evento["titulo"])
        base = (
            f"{emoji} *En {minutos} {palabra_min}:* {icono} {titulo} ({bandera} {divisa})\n"
            f"Impacto: {traducir_impacto(evento['impacto'])} · {hora_local} (Colombia)"
        )
        if clasificar_evento(evento["titulo"]).get("alcista_si_sube") is not None:
            base += (
                f"\nPronóstico: {evento['pronostico'] or 'sin dato'} | "
                f"Anterior: {evento['anterior'] or 'sin dato'}"
            )
    else:
        icono = _emoji_categoria(grupo[0]["titulo"])
        titulos = "\n".join(
            f"• {_escapar(traducir_titulo(e['titulo']))} ({traducir_impacto(e['impacto'])})"
            for e in grupo
        )
        base = (
            f"{emoji} *En {minutos} {palabra_min}:* {icono} {len(grupo)} noticias de "
            f"{bandera} {divisa} al mismo tiempo ({hora_local} Colombia):\n{titulos}"
        )

    # En el aviso de 5 minutos mandamos recordatorio corto para no repetir todo el bloque
    if es_urgente:
        return (
            f"{base}\n\n"
            f"🎯 *Activos en vigilancia:* Pares con {divisa} e índices ({indices_afectados}). "
            "Cuidado con el ensanchamiento de spreads en el minuto de publicación."
        )

    # Análisis Fundamental Completo (Aviso de 15 minutos)
    categorias_comparables = []
    nombres_vistos = set()
    for ev in grupo:
        cat = clasificar_evento(ev["titulo"])
        if cat.get("alcista_si_sube") is not None and cat["nombre"] not in nombres_vistos:
            nombres_vistos.add(cat["nombre"])
            categorias_comparables.append(cat)

    if not categorias_comparables:
        analisis = (
            "🧠 *Análisis Fundamental:*\n"
            "No publica una cifra numérica: el mercado reaccionará al **tono del mensaje**.\n"
            f"• *Tono Hawkish (duro contra la inflación / tasas altas):* Sube {divisa} ⬆️ y presiona a la baja a {indices_afectados} ⬇️.\n"
            f"• *Tono Dovish (flexible / abierto a bajar tasas):* Baja {divisa} ⬇️ e impulsa al alza a {indices_afectados} y {acciones_afectadas} ⬆️."
        )
    elif len(categorias_comparables) == 1:
        categoria = categorias_comparables[0]
        if categoria["alcista_si_sube"]:
            dir_encima, dir_debajo = "ALCISTA ⬆️", "BAJISTA ⬇️"
            razon_encima, razon_debajo = categoria["razon_alcista"], categoria["razon_bajista"]
        else:
            dir_encima, dir_debajo = "BAJISTA ⬇️", "ALCISTA ⬆️"
            razon_encima, razon_debajo = categoria["razon_bajista"], categoria["razon_alcista"]

        bolsa_encima = categoria.get("efecto_bolsa_sube", "Mixto en bolsa")
        bolsa_debajo = categoria.get("efecto_bolsa_baja", "Mixto en bolsa")

        analisis = (
            f"🧠 *Qué esperar según el resultado ({categoria['nombre']}):*\n"
            f"🔹 *Si sale MAYOR al pronóstico:*\n"
            f"  • *Divisa ({divisa}):* {dir_encima} — {razon_encima}.\n"
            f"  • *Índices y Acciones ({indices_afectados}):* {bolsa_encima}.\n\n"
            f"🔸 *Si sale MENOR al pronóstico:*\n"
            f"  • *Divisa ({divisa}):* {dir_debajo} — {razon_debajo}.\n"
            f"  • *Índices y Acciones ({indices_afectados}):* {bolsa_debajo}."
        )
        # Agregar lectura de descuento previo (Pronóstico vs Anterior) si es 1 evento
        descuento_previo = evaluar_descuento_previo(grupo[0])
        if descuento_previo:
            analisis += f"\n\n{descuento_previo}"
    else:
        lineas = [
            "🧠 *Atención: Bateria de datos simultáneos con lógica combinada:*",
            f"Activos clave: *{divisa}*, *{indices_afectados}* y *{acciones_afectadas}*.",
        ]
        for cat in categorias_comparables:
            if cat["alcista_si_sube"]:
                lineas.append(f"• *{cat['nombre']}:* Mayor a lo esperado = Sube {divisa} | {cat.get('efecto_bolsa_sube', '')}.")
            else:
                lineas.append(f"• *{cat['nombre']}:* Mayor a lo esperado = Baja {divisa} (indicador inverso) | {cat.get('efecto_bolsa_sube', '')}.")
        lineas.append(
            "💡 *Regla práctica:* Si los indicadores salen en direcciones opuestas (ej. NFP alto pero Desempleo también sube), "
            "el precio suele dar un latigazo en ambas direcciones (whipsaw). Espera a que cierre la primera vela de 5 o 15 min."
        )
        analisis = "\n".join(lineas)

    return f"{base}\n\n{analisis}"


def mensaje_publicacion(grupo):
    divisa = grupo[0]["divisa"]
    bandera = _bandera(divisa)
    if len(grupo) == 1:
        evento = grupo[0]
        titulo = _escapar(traducir_titulo(evento["titulo"]))
        icono = _emoji_categoria(evento["titulo"])
        if clasificar_evento(evento["titulo"]).get("alcista_si_sube") is None:
            return f"🎤 *Empezando ahora:* {titulo} ({bandera} {divisa})"
        return (
            f"💥 *Publicado ahora:* {icono} {titulo} ({bandera} {divisa})\n"
            f"Pronóstico: {evento['pronostico'] or 'sin dato'} | "
            f"Anterior: {evento['anterior'] or 'sin dato'}"
        )
    icono = _emoji_categoria(grupo[0]["titulo"])
    titulos = "\n".join(f"• {_escapar(traducir_titulo(e['titulo']))}" for e in grupo)
    return f"💥 *Publicado ahora ({bandera} {divisa}):* {icono} {len(grupo)} noticias a la vez\n{titulos}"


def mensaje_reaccion(evento, analisis):
    titulo = _escapar(traducir_titulo(evento["titulo"]))
    divisa = evento["divisa"]
    bandera = _bandera(divisa)
    icono = _emoji_categoria(evento["titulo"])
    emoji = {"alcista": "🟢", "bajista": "🔴", "neutral": "⚪"}[analisis["direccion"]]
    info_activos = MAPA_ACTIVOS.get(divisa, {})
    acciones_txt = info_activos.get("acciones_sensibles", "Acciones principales")

    linea_divisa = (
        f"💱 *Divisa ({bandera} {divisa}):* *{analisis['direccion'].upper()}* — {analisis['razon_divisa']}."
    )
    if analisis.get("confirmacion"):
        linea_divisa += f" _({analisis['confirmacion']})_."

    return (
        f"{emoji} *RESULTADO PUBLICADO: {icono} {titulo}* ({bandera} {divisa})\n"
        f"📊 *Actual:* `{evento['actual']}` | *Pronóstico:* `{evento['pronostico']}` | *Anterior:* `{evento['anterior']}`\n\n"
        f"{linea_divisa}\n\n"
        f"📈 *Índices y Acciones:* {analisis['impacto_bolsa']}.\n"
        f"🏢 *Sectores más sensibles:* {acciones_txt}.\n\n"
        f"{analisis['descuento_txt']}"
    )


def mensaje_reporte_matutino(eventos_hoy):
    if not eventos_hoy:
        return (
            "🌅 *Panorama Fundamental del Día*\n\n"
            "No hay noticias de impacto Medio ni Alto programadas hoy para tus "
            "divisas principales. El mercado se moverá principalmente por flujo "
            "técnico y sentimiento general."
        )

    eventos_hoy = sorted(eventos_hoy, key=lambda e: e["fecha"])
    altos = [e for e in eventos_hoy if e["impacto"] == "High"]
    medios = [e for e in eventos_hoy if e["impacto"] == "Medium"]

    lineas = [
        "🌅 *PANORAMA FUNDAMENTAL DEL DÍA (Colombia)*",
        f"🗓️ Hoy tenemos *{len(altos)} noticias de Alto Impacto* 🔴 y *{len(medios)} de Impacto Medio* 🟠:\n",
    ]

    divisas_del_dia = []
    for ev in eventos_hoy:
        hora = ev["fecha"].astimezone(ZONA_HORARIA).strftime("%H:%M")
        titulo = _escapar(traducir_titulo(ev["titulo"]))
        bandera = _bandera(ev["divisa"])
        icono = _emoji_categoria(ev["titulo"])
        imp_icono = "🔴" if ev["impacto"] == "High" else "🟠"
        pron = f" (Esp: {ev['pronostico']} | Ant: {ev['anterior']})" if ev["pronostico"] else ""
        lineas.append(f"{imp_icono} `{hora}` | {bandera} *{ev['divisa']}* | {icono} {titulo}{pron}")
        if ev["divisa"] not in divisas_del_dia:
            divisas_del_dia.append(ev["divisa"])

    # Resumen fundamental ejecutivo del día
    lineas.append("\n🧠 *Guía Fundamental Rápida para Hoy:*")
    for div in divisas_del_dia[:4]:
        info = MAPA_ACTIVOS.get(div, {})
        evs_div = [e for e in eventos_hoy if e["divisa"] == div]
        nombres_cat = list(dict.fromkeys(clasificar_evento(e["titulo"])["nombre"] for e in evs_div))
        resumen_cats = ", ".join(nombres_cat[:2])
        lineas.append(
            f"• *{_bandera(div)} {div} ({info.get('indices', '')}):* Foco en *{resumen_cats}*. "
            f"{info.get('nota_pais', '')}"
        )

    lineas.append(
        "\n💡 _Recuerda: Antes de cada evento recibirás si el dato ya viene descontado "
        "(Priced-in) y tras la publicación el impacto real sobre la divisa, índices y acciones._"
    )
    return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════════════
# 8. ENVÍO Y TRABAJOS DEL SCHEDULER
# ══════════════════════════════════════════════════════════════════════════
def _enviar(mensaje, chat_id_destino=None):
    destino = chat_id_destino or CHAT_ID
    try:
        bot.send_message(destino, mensaje, parse_mode="Markdown")
    except Exception as e:
        log.error("Fallo al enviar mensaje a Telegram: %s", e)
        _estado_salud["ultimo_error"] = f"envío: {e}"


def job_aviso_previo(grupo, minutos):
    _enviar(mensaje_previo(grupo, minutos))


def job_publicacion(grupo):
    _enviar(mensaje_publicacion(grupo))


def _clave_reaccion(evento):
    return f"{evento['divisa']}|{evento['titulo']}|{evento['fecha'].isoformat()}"


def job_reaccion(evento):
    clave = _clave_reaccion(evento)
    if _ya_reaccion_enviada(clave):
        return

    eventos_frescos = obtener_calendario()
    actualizado = evento
    for ev in eventos_frescos:
        if (
            ev["titulo"] == evento["titulo"]
            and ev["divisa"] == evento["divisa"]
            and ev["fecha"] == evento["fecha"]
        ):
            actualizado = ev
            break

    analisis = analizar_resultado(actualizado)
    if analisis is None:
        return

    _marcar_reaccion_enviada(clave)
    _enviar(mensaje_reaccion(actualizado, analisis))


def job_reporte_matutino():
    eventos = obtener_calendario()
    hoy = eventos_relevantes(eventos, solo_hoy=True)
    _estado_salud["eventos_detectados_hoy"] = len(hoy)
    _enviar(mensaje_reporte_matutino(hoy))


# ══════════════════════════════════════════════════════════════════════════
# 9. COMANDOS INTERACTIVOS DE TELEGRAM (/hoy, /resumen, /estado)
# ══════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=["start", "ayuda", "help"])
def cmd_start(message):
    texto = (
        "🤖 *Bot de Calendario Económico y Análisis Fundamental*\n\n"
        "Monitoreo noticias de **Medio y Alto impacto** en tiempo real y te explico:\n"
        "1️⃣ Qué esperar en la *Divisa*, *Índices Bursátiles* y *Acciones*.\n"
        "2️⃣ Si el resultado *ya está descontado (Priced-in)* o si hay sorpresa real.\n\n"
        "*Comandos disponibles:*\n"
        "• /hoy o /resumen — Ver las noticias y el análisis fundamental de hoy\n"
        "• /estado — Ver estado técnico del bot y sincronización"
    )
    _enviar(texto, chat_id_destino=message.chat.id)


@bot.message_handler(commands=["hoy", "resumen"])
def cmd_hoy(message):
    eventos = obtener_calendario()
    hoy = eventos_relevantes(eventos, solo_hoy=True)
    _estado_salud["eventos_detectados_hoy"] = len(hoy)
    _enviar(mensaje_reporte_matutino(hoy), chat_id_destino=message.chat.id)


@bot.message_handler(commands=["estado"])
def cmd_estado(message):
    texto = (
        "⚙️ *Estado del Bot Fundamental*\n"
        f"• *Última sincronización:* `{_estado_salud['ultima_sincronizacion']}`\n"
        f"• *Eventos hoy (Medio/Alto):* `{_estado_salud['eventos_detectados_hoy']}`\n"
        f"• *Alertas en cola:* `{len(scheduler.get_jobs())}`\n"
        f"• *Último error:* `{_estado_salud['ultimo_error'] or 'Ninguno'}`"
    )
    _enviar(texto, chat_id_destino=message.chat.id)


# ══════════════════════════════════════════════════════════════════════════
# 10. SINCRONIZACIÓN Y PROGRAMACIÓN DINÁMICA
# ══════════════════════════════════════════════════════════════════════════
scheduler = BackgroundScheduler(timezone=ZONA_HORARIA)


def sincronizar_calendario():
    ahora = datetime.now(pytz.utc)
    estado = _cargar_estado_disco()
    eventos = obtener_calendario()
    relevantes = eventos_relevantes(eventos, solo_hoy=False)
    hoy = eventos_relevantes(eventos, solo_hoy=True)
    _estado_salud["eventos_detectados_hoy"] = len(hoy)
    ventana_reaccion = max(REINTENTOS_REACCION_MIN)

    grupos = {}
    for ev in relevantes:
        grupos.setdefault((ev["divisa"], ev["fecha"]), []).append(ev)

    nuevos = 0
    for (divisa, fecha), grupo in grupos.items():
        clave_grupo = f"{divisa}|{fecha.isoformat()}|grupo"
        ya_paso = fecha < ahora - timedelta(minutes=ventana_reaccion)
        tiene_alto_impacto = any(e["impacto"] == "High" for e in grupo)

        if not _ya_programado(estado, clave_grupo):
            if not ya_paso:
                # Si el grupo es solo de impacto Medio, enviamos solo el aviso
                # de 15 min antes (con el análisis completo) y la reacción post-dato,
                # para no saturarte con aviso a los 5 min ni al minuto 0.
                minutos_a_programar = AVISOS_PREVIOS_MIN if tiene_alto_impacto else [15]
                for minutos in minutos_a_programar:
                    momento = fecha - timedelta(minutes=minutos)
                    if momento > ahora:
                        scheduler.add_job(
                            job_aviso_previo, "date", run_date=momento,
                            args=[grupo, minutos],
                            id=f"{clave_grupo}|previo{minutos}", replace_existing=True,
                        )
                if fecha > ahora and tiene_alto_impacto:
                    scheduler.add_job(
                        job_publicacion, "date", run_date=fecha,
                        args=[grupo], id=f"{clave_grupo}|ahora", replace_existing=True,
                    )
            _marcar_programado(estado, clave_grupo)

        for ev in grupo:
            clave_ev = _clave_reaccion(ev)
            if _ya_programado(estado, clave_ev):
                continue
            if ev["fecha"] < ahora - timedelta(minutes=ventana_reaccion):
                _marcar_programado(estado, clave_ev)
                continue
            for idx, minutos_despues in enumerate(REINTENTOS_REACCION_MIN):
                scheduler.add_job(
                    job_reaccion, "date",
                    run_date=ev["fecha"] + timedelta(minutes=minutos_despues),
                    args=[ev], id=f"{clave_ev}|reaccion{idx}", replace_existing=True,
                )
            _marcar_programado(estado, clave_ev)
            nuevos += 1

    _estado_salud["ultima_sincronizacion"] = datetime.now(ZONA_HORARIA).isoformat()
    _estado_salud["avisos_programados_activos"] = len(scheduler.get_jobs())
    log.info(
        "Sincronización completada: %d eventos relevantes (%d hoy) en %d grupos, %d nuevos programados.",
        len(relevantes), len(hoy), len(grupos), nuevos,
    )


def programar_trabajos_fijos():
    scheduler.add_job(job_reporte_matutino, "cron", hour=6, minute=0, id="reporte_matutino")
    scheduler.add_job(
        sincronizar_calendario, "interval",
        minutes=INTERVALO_SINCRONIZACION_MIN, id="sincronizacion_periodica",
    )
    scheduler.start()
    sincronizar_calendario()


# ══════════════════════════════════════════════════════════════════════════
# 11. EJECUCIÓN PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info("Arrancando bot de análisis fundamental...")

    programar_trabajos_fijos()

    hilo_web = threading.Thread(target=run_flask, daemon=True)
    hilo_web.start()

    log.info("Servidor web y programador activos. Iniciando polling de Telegram...")
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            log.error("El polling de Telegram se cortó (%s); reintentando en 15s.", e)
            _estado_salud["ultimo_error"] = f"polling: {e}"
            time.sleep(15)
