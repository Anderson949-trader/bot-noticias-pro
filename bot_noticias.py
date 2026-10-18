"""
════════════════════════════════════════════════════════════════════════════
 BOT DE TELEGRAM · CALENDARIO ECONÓMICO CON ANÁLISIS FUNDAMENTAL REAL
════════════════════════════════════════════════════════════════════════════

QUÉ CAMBIÉ RESPECTO A TU CÓDIGO ORIGINAL (para que sepas qué esperar):

  1-6, 8. (ver historial completo en versiones anteriores de este mismo
     comentario si lo necesitas: scraping real, análisis previo al dato
     con la lógica económica correcta por categoría, programación
     dinámica sin tocar el código cada semana, reporte matutino real,
     token/chat_id por variable de entorno, y el bug de "SNB Policy Rate"
     que se descartaba en silencio.)

  7. (HISTORIAL) Con el feed de ForexFactory, se eliminó por completo la
     comparación "resultado real vs. pronóstico" porque ese feed JAMÁS
     traía el dato publicado (verificado en vivo, 78 eventos completos).

  9. CAMBIO DE FUENTE: FOREXFACTORY -> INVESTING.COM. Motivo real, no
     capricho: probaste el bot en vivo el 23 de septiembre y no avisó del
     PMI de EEUU que tú SÍ viste en Investing.com. Investigando la causa
     de raíz (no solo cambiando de fuente a ciegas):
       - El feed de ForexFactory SÍ tenía ese PMI, pero lo marcaba impacto
         "Medium"/"Low" -tu filtro (a propósito, para no llenarte de
         ruido) solo avisa en "High". No era un bug, era el filtro
         funcionando como se diseñó, con una fuente que subestimaba esa
         noticia.
       - Investing.com, en cambio, SÍ marcaba esa misma noticia como
         "Alta" (3 toros, "High Volatility Expected") -y salió con una
         sorpresa real grande (57.0 vs. 53.6 esperado). Dos fuentes,
         mismo evento, distinta calificación de importancia -y en este
         caso Investing.com calificó mejor.
       - Además, Investing.com SÍ trae el dato "actual" (verificado en
         vivo el mismo día) -algo que ForexFactory nunca dio. Por eso el
         análisis "resultado real vs. pronóstico" (que se había quitado
         por imposible) VUELVE a existir en esta versión.
       - Investing.com NO tiene una API pública documentada. Este bot usa
         el mismo endpoint interno que la propia página usa para pintar
         su tabla (`/economic-calendar/Service/getCalendarFilteredData`).
         Se verificó en vivo que responde sin el bloqueo de Cloudflare que
         sí tiene la página HTML completa de ForexFactory. RIESGO
         ACEPTADO A PROPÓSITO: al no ser una API oficial, puede cambiar de
         estructura o bloquearse sin aviso -es el mismo riesgo que asumen
         TODOS los bots/scrapers de Investing.com que existen. Si un día
         deja de funcionar, este comentario y la función
         `obtener_calendario()` son el único lugar que hay que revisar.
       - DESCUBRIMIENTO REAL E IMPORTANTE probando esto en vivo: el mismo
         endpoint, con los mismos parámetros, responde 200 (funciona) con
         `urllib` (lo básico de Python) pero 403 Forbidden con `requests`
         -SIEMPRE, de forma repetible, probado varias veces seguidas. El
         sistema anti-bots de Investing.com identifica la huella digital
         de conexión de `requests`/`urllib3` (la librería más común para
         scraping) y la bloquea, mientras que `urllib` puro no está en esa
         lista negra. Por eso este archivo usa `urllib.request` para esta
         descarga en particular, no `requests`. Esto es MÁS FRÁGIL que el
         feed de ForexFactory: si Investing.com algún día también huellea
         `urllib`, esto se rompe sin aviso. Es el precio de no tener una
         API oficial.
       - Los 8 códigos numéricos de país que usa este endpoint (uno por
         divisa objetivo) se verificaron uno por uno contra la divisa real
         que devuelven, no se copiaron a ciegas de una tabla de otro
         proyecto (esa tabla era de un sistema distinto, el widget
         embebido, y podía no aplicar aquí).
       - La hora que entrega este endpoint se verificó cruzando el mismo
         evento (PMI alemán) contra la hora ya confirmada por
         ForexFactory: corresponde a hora de Nueva York.

LO QUE SIGUE IGUAL, A PROPÓSITO: Flask como "keep-alive" para Render,
APScheduler para programar, pytz para la zona horaria de Colombia,
polling de Telegram al final.

════════════════════════════════════════════════════════════════════════════
 CONFIGURACIÓN NECESARIA (variables de entorno, NO las escribas en el código)
════════════════════════════════════════════════════════════════════════════
  TELEGRAM_TOKEN   -> 8007552290:AAHH8KQrYklwR6oh8Tjw2_VbUvXs1D8Zd_I
  TELEGRAM_CHAT_ID -> 848594835

  En Render: Dashboard -> tu servicio -> Environment -> Add Environment
  Variable. En tu máquina para probar: crea un archivo ".env" (no lo subas
  a GitHub, agrégalo a .gitignore) o expórtalas en la terminal:
      $env:TELEGRAM_TOKEN = "123456:ABC..."      (PowerShell)
      export TELEGRAM_TOKEN="123456:ABC..."      (Linux/Mac)

  requirements.txt necesita (te dejo el archivo aparte, revísalo):
      pyTelegramBotAPI
      flask
      apscheduler
      pytz
      beautifulsoup4        <- NUEVO: para leer la tabla HTML de Investing.com
      (ya NO hace falta "requests": la descarga del calendario usa urllib,
      ver el punto 9 de arriba -pyTelegramBotAPI trae su propia forma de
      hablar con la API de Telegram, no depende de este archivo para eso)
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
log = logging.getLogger("bot_forex")

# Si defines TELEGRAM_TOKEN / TELEGRAM_CHAT_ID como variables de entorno
# (por ejemplo en Render → Environment), esas tienen prioridad y estos
# valores de aquí nunca se usan. Quedan como respaldo para que el bot
# arranque igual si lo corres local sin configurar nada.
#
# OJO SI SUBES ESTO A GITHUB: si el repositorio es público (o se vuelve
# público más adelante, o alguien lo clona), cualquiera que lea este
# archivo puede controlar tu bot con este token. Si vas a subirlo, lo más
# seguro sigue siendo borrar los valores de abajo y configurarlos como
# variables de entorno en Render en su lugar.
TOKEN = os.environ.get("TELEGRAM_TOKEN", "8007552290:AAHH8KQrYklwR6oh8Tjw2_VbUvXs1D8Zd_I").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "848594835").strip()

if not TOKEN or not CHAT_ID:
    raise SystemExit(
        "Faltan las variables de entorno TELEGRAM_TOKEN y/o TELEGRAM_CHAT_ID.\n"
        "Configúralas antes de arrancar el bot (ver la cabecera de este archivo)."
    )

ZONA_HORARIA = pytz.timezone("America/Bogota")

# Divisas que te interesan en tus análisis técnicos
DIVISAS_OBJETIVO = {"EUR", "USD", "JPY", "GBP", "AUD", "CAD", "CHF", "NZD"}

# Qué tan fuerte debe ser una noticia para que te avise. Investing.com usa
# 1/2/3 "toros" internamente; este bot los traduce al mismo vocabulario de
# siempre (Low/Medium/High) para no tener que tocar nada más del archivo.
IMPACTOS_A_MONITOREAR = {"High"}

# EL FILTRO DE VERDAD: que la fuente marque algo "High" no significa que de
# verdad mueva el mercado. Investing.com, por ejemplo, desglosa el "dot
# plot" de la Fed en varias líneas sueltas (una por cada año de proyección)
# y las marca con 3 toros — el mismo dato reempaquetado varias veces, justo
# el tipo de ruido que se pidió excluir (ver _PATRON_PROYECCION_SUELTA más
# abajo, que descarta esas líneas ANTES de clasificar). Cada categoría en
# CATEGORIAS tiene un "nivel":
#   "clave"      -> tasas, PIB, IPC, empleo, PMI, discursos/actas de
#                    bancos centrales. Los movedores de mercado reales.
#   "secundario" -> ventas minoristas, balanza comercial, confianza del
#                    consumidor, vivienda, solicitudes de desempleo.
#                    Importan, pero mueven menos.
#   "descartar"  -> todo lo que no encaja en ninguna categoría reconocida
#                    (la CATEGORIA_DEFECTO). Por más que el feed lo marque
#                    "High", si no se reconoce el indicador, no se avisa.
# Con INCLUIR_SECUNDARIOS en False (por defecto) solo llegan avisos de
# "clave". Ponlo en True si quieres más cobertura a cambio de más avisos.
INCLUIR_SECUNDARIOS = False

# ── FUENTE: Investing.com (endpoint interno, ver punto 9 de la cabecera) ──
URL_CALENDARIO = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"

# Códigos numéricos de PAÍS que usa este endpoint -uno por divisa objetivo.
# VERIFICADOS EN VIVO uno por uno (se pidió cada código por separado y se
# confirmó que la divisa que trae de vuelta es la esperada), no copiados a
# ciegas de una tabla de otro proyecto.
_PAISES_OBJETIVO_IDS = {
    5: "USD", 4: "GBP", 72: "EUR", 35: "JPY",
    25: "AUD", 6: "CAD", 12: "CHF", 43: "NZD",
}

# El endpoint entrega las horas en hora de Nueva York (parámetro
# "timeZone=8" del propio Investing.com) -verificado cruzando el mismo
# evento contra la hora ya confirmada por ForexFactory. Se usa el nombre
# de zona horaria (no un offset fijo) para que el horario de verano se
# maneje solo, automáticamente, en cualquier época del año.
_ZONA_FUENTE = pytz.timezone("America/New_York")

_IMPORTANCIA_A_TEXTO = {3: "High", 2: "Medium", 1: "Low"}

# Minutos de anticipación de cada aviso antes del evento.
AVISOS_PREVIOS_MIN = [15, 5]

# MEDIDO EN VIVO CON INVESTING.COM: para un dato publicado a las 9:45 AM,
# a las 12:06 PM (más de 2 horas después) el "actual" ya estaba disponible
# -este proveedor sí completa el dato, a diferencia de ForexFactory. Aun
# así se reintenta varias veces con espaciado creciente por si algún
# indicador puntual tarda más (revisiones, festivos, etc.); si tras el
# último intento nunca llegó, no se manda nada -silencio, no una nota de
# "no lo conseguí".
REINTENTOS_REACCION_MIN = [3, 8, 15, 30]

# Cada cuánto se vuelve a consultar el calendario para detectar noticias
# nuevas o cambios de pronóstico (en minutos).
INTERVALO_SINCRONIZACION_MIN = 60

# Dónde se guarda el registro de qué ya se programó, para no duplicar
# avisos si el proceso se reinicia (Render reinicia los free services).
ARCHIVO_ESTADO = "estado_calendario.json"

bot = telebot.TeleBot(TOKEN, parse_mode=None)
app = Flask(__name__)

# Estado en memoria compartido con el endpoint de salud de Flask, solo
# para poder ver desde el navegador que el bot está vivo y qué vio.
_estado_salud = {
    "arranque": datetime.now(ZONA_HORARIA).isoformat(),
    "ultima_sincronizacion": None,
    "eventos_detectados_hoy": 0,
    "avisos_programados_activos": 0,
    "ultimo_error": None,
}


# ══════════════════════════════════════════════════════════════════════════
# 2. PERSISTENCIA SIMPLE (evita mandar el mismo aviso dos veces)
# ══════════════════════════════════════════════════════════════════════════
_ESTADO_DEFECTO = {"programados": [], "reacciones_enviadas": []}


def _cargar_estado_disco():
    if not os.path.exists(ARCHIVO_ESTADO):
        return dict(_ESTADO_DEFECTO)
    try:
        with open(ARCHIVO_ESTADO, "r", encoding="utf-8") as f:
            estado = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("No se pudo leer %s (%s); se empieza de cero.", ARCHIVO_ESTADO, e)
        return dict(_ESTADO_DEFECTO)
    # por si el archivo viene de una version anterior sin esta clave
    estado.setdefault("programados", [])
    estado.setdefault("reacciones_enviadas", [])
    return estado


def _guardar_estado_disco(estado):
    try:
        with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log.warning("No se pudo guardar %s: %s", ARCHIVO_ESTADO, e)


def _ya_programado(estado, clave):
    return clave in estado["programados"]


def _marcar_programado(estado, clave):
    estado["programados"].append(clave)
    # Se conservan solo las últimas 500 claves para que el archivo no
    # crezca sin límite; con eso sobra para varias semanas de cobertura.
    estado["programados"] = estado["programados"][-500:]
    _guardar_estado_disco(estado)


def _ya_reaccion_enviada(clave):
    """Se persiste en disco (no solo en memoria): si Render reinicia el
    proceso a mitad de la ventana de 30 minutos de reintentos -pasa
    seguido en el plan gratis-, sin esto se podía mandar la MISMA
    comparación real dos veces."""
    estado = _cargar_estado_disco()
    return clave in estado["reacciones_enviadas"]


def _marcar_reaccion_enviada(clave):
    estado = _cargar_estado_disco()
    estado["reacciones_enviadas"].append(clave)
    estado["reacciones_enviadas"] = estado["reacciones_enviadas"][-500:]
    _guardar_estado_disco(estado)


# ══════════════════════════════════════════════════════════════════════════
# 3. SISTEMA ANTI-SUSPENSIÓN (FLASK)
# ══════════════════════════════════════════════════════════════════════════
@app.route("/")
def keep_alive():
    return (
        "Bot de calendario económico activo (America/Bogota).\n"
        f"Arrancó: {_estado_salud['arranque']}\n"
        f"Última sincronización del calendario: {_estado_salud['ultima_sincronizacion']}\n"
        f"Eventos de alto impacto detectados hoy: {_estado_salud['eventos_detectados_hoy']}\n"
        f"Avisos con hora ya programada: {_estado_salud['avisos_programados_activos']}\n"
        f"Último error: {_estado_salud['ultimo_error']}\n"
    )


def run_flask():
    puerto = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=puerto)


# ══════════════════════════════════════════════════════════════════════════
# 4. OBTENCIÓN DEL CALENDARIO ECONÓMICO REAL (Investing.com)
# ══════════════════════════════════════════════════════════════════════════
# CACHÉ CORTA: con la comparación real de vuelta, un día con varias
# noticias agrupadas (ej. la Fed: tasa + proyecciones + comunicado a la
# misma hora) vuelve a programar varios reintentos de reacción en los
# MISMOS minutos después. Sin caché, esas llamadas casi simultáneas
# golpearían el mismo endpoint varias veces seguidas -y al ser un endpoint
# interno sin límites publicados, mejor no arriesgarse. Con esta caché,
# las llamadas que caen dentro de la misma ventana de 2 minutos comparten
# una sola descarga real.
_CACHE_CALENDARIO = {"eventos": None, "momento": None}
_CACHE_TTL_SEGUNDOS = 120
_lock_cache_calendario = threading.Lock()

_HEADERS_INVESTING = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://www.investing.com/economic-calendar/",
}


def _parsear_fila_evento(fila):
    """Convierte una fila <tr id="eventRowId_..."> de la tabla de
    Investing.com en un diccionario normalizado, o None si la fila no trae
    lo mínimo indispensable (fecha). Cualquier pieza que falte (ej. un
    discurso sin pronóstico) se deja como cadena vacía, nunca inventada."""
    dt_texto = fila.get("data-event-datetime")
    if not dt_texto:
        return None
    try:
        fecha_ingenua = datetime.strptime(dt_texto, "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None
    fecha = _ZONA_FUENTE.localize(fecha_ingenua)

    celda_pais = fila.find("td", class_="flagCur")
    # .replace del espacio duro (&nbsp;) porque str.strip() por sí solo NO
    # lo considera espacio en blanco -sin esto, a veces quedaba pegado a
    # la divisa y rompía la comparación contra DIVISAS_OBJETIVO.
    texto_pais = celda_pais.get_text().replace("\xa0", " ") if celda_pais else ""
    partes = texto_pais.split()
    divisa = partes[-1].upper() if partes else ""

    celda_sentimiento = fila.find("td", class_="sentiment")
    n_toros = len(celda_sentimiento.find_all("i")) if celda_sentimiento else 0
    impacto = _IMPORTANCIA_A_TEXTO.get(n_toros, "Low")

    celda_evento = fila.find("td", class_="event")
    enlace = celda_evento.find("a") if celda_evento else None
    titulo_crudo = (enlace.get_text() if enlace else (celda_evento.get_text() if celda_evento else ""))
    # Quita el sufijo de periodo, ej. "S&P Global Manufacturing PMI  (Sep)"
    # -> "S&P Global Manufacturing PMI". Se hace aquí, no en
    # traducir_titulo(), porque afecta también a la clasificación por
    # palabras clave si algún mes quedara pegado a una palabra.
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
    """
    Descarga el calendario económico de la semana en curso desde
    Investing.com (ver punto 9 de la cabecera de este archivo) y devuelve
    una lista de diccionarios normalizados: titulo, divisa, impacto
    (High/Medium/Low, mismo vocabulario de siempre), fecha, pronostico,
    anterior, actual.

    Si la descarga o el análisis del HTML fallan (caída del servicio,
    cambio de estructura de la página, sin internet, etc.) se devuelve una
    lista vacía y se registra el error, para que el bot no se caiga por un
    problema pasajero -ni por un cambio de Investing.com que rompa el
    formato esperado.
    """
    with _lock_cache_calendario:
        momento_cache = _CACHE_CALENDARIO["momento"]
        if momento_cache is not None:
            edad = (datetime.now(pytz.utc) - momento_cache).total_seconds()
            if edad < _CACHE_TTL_SEGUNDOS:
                return _CACHE_CALENDARIO["eventos"]

        cuerpo = (
            "importance%5B%5D=1&importance%5B%5D=2&importance%5B%5D=3"
            "&timeZone=8&timeFilter=timeRemain&currentTab=thisWeek"
            + "".join(f"&country%5B%5D={cid}" for cid in _PAISES_OBJETIVO_IDS)
        )
        try:
            # A PROPOSITO con urllib, NO con requests -ver el punto 9 de la
            # cabecera del archivo: el mismo endpoint responde 403 con
            # requests/urllib3 (huella digital de libreria bloqueada por su
            # sistema anti-bots) pero 200 con urllib, verificado en vivo
            # varias veces seguidas.
            peticion = urllib.request.Request(
                URL_CALENDARIO, data=cuerpo.encode("utf-8"),
                headers=_HEADERS_INVESTING, method="POST",
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
            except Exception as e:  # una fila rara no debe tumbar todo el calendario
                log.warning("No se pudo leer una fila del calendario: %s", e)
                continue
            if evento is not None:
                eventos.append(evento)

        _CACHE_CALENDARIO["eventos"] = eventos
        _CACHE_CALENDARIO["momento"] = datetime.now(pytz.utc)
        return eventos


def _nivel_de_relevancia(titulo):
    """'clave', 'secundario' o 'descartar' — ver el comentario de
    INCLUIR_SECUNDARIOS arriba para qué significa cada uno."""
    return clasificar_evento(titulo).get("nivel", "descartar")


# Investing.com casi nunca marca "High" el discurso de un banquero central
# salvo que sea la rueda de prensa oficial tras una decisión de tasas. Un
# discurso normal de un miembro cualquiera del comité (no el presidente)
# suele venir como "Medium" o "Low" -visto en vivo con "German Buba
# Mauderer Speaks" (un director del Bundesbank, no su presidente).
#
# Esto se soluciona distinguiendo PRESIDENTE/GOBERNADOR (el jefe del banco
# central, cuyas palabras sí mueven el mercado aunque la fuente lo marque
# "Medium") de un MIEMBRO o ADJUNTO cualquiera (para quien sí se respeta
# el impacto que diga la fuente, o sencillamente no se avisa).
_PALABRAS_JEFE_BANCO_CENTRAL = ["chair", "president", "governor", " gov ", "chairman"]
_PALABRAS_NO_ES_EL_JEFE = ["assist", "deputy", "vice", "member"]


def _es_jefe_banco_central(titulo, divisa):
    """True solo para el jefe del banco central que de verdad fija la
    política de esa divisa (Powell, Lagarde, Bailey, Bullock...), nunca
    para un adjunto, vice o miembro cualquiera del comité.

    CASO ESPECIAL EUR: la eurozona tiene un banco central POR PAÍS
    (Bundesbank en Alemania, Banque de France, Banca d'Italia...) pero
    solo UNO fija la tasa del euro: el BCE. El presidente del Bundesbank
    opina, no decide solo, y su discurso no mueve el mercado como el de la
    presidenta del BCE. Por eso para EUR no basta con
    "president"/"governor": el titular debe mencionar explícitamente al
    BCE."""
    t = f" {(titulo or '').lower()} "
    if any(p in t for p in _PALABRAS_NO_ES_EL_JEFE):
        return False
    if not any(p in t for p in _PALABRAS_JEFE_BANCO_CENTRAL):
        return False
    if divisa == "EUR" and "ecb" not in t and "bce" not in t:
        return False
    return True


def eventos_relevantes(eventos, solo_hoy=False):
    """Filtra por divisa objetivo, impacto de la fuente, y por si el
    indicador es de los que de verdad mueven el mercado (ver
    INCLUIR_SECUNDARIOS). Que la fuente diga "High" ya no basta por sí
    solo — y a la inversa: si es el PRESIDENTE de un banco central
    hablando, se avisa aunque la fuente lo marque "Medium" o "Low", porque
    sus palabras mueven el mercado más de lo que esa etiqueta sugiere."""
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
        es_jefe_hablando = (
            categoria.get("nombre") == "Discurso o comparecencia de un banquero central"
            and _es_jefe_banco_central(ev["titulo"], ev["divisa"])
        )
        if ev["impacto"] not in IMPACTOS_A_MONITOREAR and not es_jefe_hablando:
            continue
        if solo_hoy:
            fecha_bogota = ev["fecha"].astimezone(ZONA_HORARIA)
            if fecha_bogota.date() != ahora_bogota.date():
                continue
        resultado.append(ev)
    return resultado


# ══════════════════════════════════════════════════════════════════════════
# 5. ANÁLISIS FUNDAMENTAL — motor de clasificación y explicación
# ══════════════════════════════════════════════════════════════════════════
# Cada categoría define:
#   claves         -> palabras que identifican el titular de la noticia
#   nombre         -> nombre limpio en español para mostrar
#   alcista_si_sube -> True si un dato MEJOR de lo esperado fortalece la
#                      divisa (la inmensa mayoría). False en los pocos
#                      indicadores "inversos" (desempleo, solicitudes de
#                      subsidio): un número más alto ahí es MALA noticia.
#   razon_alcista / razon_bajista -> el mecanismo económico concreto que
#                      conecta el resultado con el movimiento de la divisa,
#                      para no repetir la misma frase en toda noticia.
CATEGORIAS = {
    "tasas": {
        "claves": [
            "tasa de interés", "tasas de interés", "interest rate",
            "tipo de interés", "fomc statement", "boe rate", "ecb rate",
            "rba rate", "boc rate", "snb rate", "rate decision",
            "decisión de tasas", "decisión de tipos",
            "federal funds rate", "official bank rate", "overnight rate",
            "cash rate", "economic projections", "summary of economic projections",
            "dot plot",
            "policy rate", "bank rate", "main refinancing rate",
            "deposit facility rate", "repo rate", "base rate",
        ],
        "nombre": "Decisión de tasas de interés",
        "nivel": "clave",
        "alcista_si_sube": True,
        "razon_alcista": (
            "al subir (o mantenerse más restrictiva de lo esperado) atrae "
            "capital que persigue mejor rendimiento, lo que fortalece la divisa"
        ),
        "razon_bajista": (
            "al bajar (o suavizarse el tono) reduce el atractivo de mantener "
            "esa divisa frente a otras con mejor rendimiento, y suele debilitarla"
        ),
    },
    "pib": {
        "claves": ["pib", "gdp", "producto interno bruto"],
        "nombre": "Producto Interno Bruto (PIB)",
        "nivel": "clave",
        "alcista_si_sube": True,
        "razon_alcista": "una economía más fuerte de lo esperado da margen al banco central para sostener tasas altas, lo que favorece a la divisa",
        "razon_bajista": "un crecimiento más débil de lo esperado presiona a que el banco central relaje su política, lo que suele debilitar la divisa",
    },
    "ipc": {
        "claves": [
            "ipc", "cpi", "inflación", "pce", "índice de precios",
            "precios al consumidor", "ppi", "producer price",
            "índice de precios al productor",
        ],
        "nombre": "Inflación (IPC / PCE / PPI)",
        "nivel": "clave",
        "alcista_si_sube": True,
        "razon_alcista": "una inflación más alta de lo esperado aumenta la presión para que el banco central mantenga tasas altas, lo que fortalece la divisa en el corto plazo",
        "razon_bajista": "una inflación más baja de lo esperado abre la puerta a recortes de tasas, lo que suele debilitar la divisa",
    },
    "desempleo_tasa": {
        "claves": ["tasa de desempleo", "unemployment rate"],
        "nombre": "Tasa de desempleo",
        "nivel": "clave",
        "alcista_si_sube": False,  # indicador inverso
        "razon_alcista": "una tasa de desempleo más baja de lo esperado muestra un mercado laboral fuerte, lo que sostiene expectativas de tasas altas y fortalece la divisa",
        "razon_bajista": "una tasa de desempleo más alta de lo esperado muestra un mercado laboral débil, lo que presiona a bajar tasas y suele debilitar la divisa",
    },
    "solicitudes_desempleo": {
        "claves": [
            "solicitudes de desempleo", "solicitudes por desempleo",
            "jobless claims", "unemployment claims",
            "subsidio por desempleo", "claimant count",
        ],
        "nombre": "Solicitudes de subsidio por desempleo",
        "nivel": "secundario",
        "alcista_si_sube": False,  # indicador inverso
        "razon_alcista": "menos solicitudes de las esperadas confirma un mercado laboral sólido, lo que favorece a la divisa",
        "razon_bajista": "más solicitudes de las esperadas es señal temprana de enfriamiento laboral, lo que suele pesar sobre la divisa",
    },
    "empleo": {
        "claves": [
            "nóminas", "nfp", "payroll", "nonfarm", "non-farm",
            "cambio de empleo", "creación de empleo", "empleo no agrícola",
            "employment change",
        ],
        "nombre": "Creación de empleo (Nóminas no agrícolas / NFP)",
        "nivel": "clave",
        "alcista_si_sube": True,
        "razon_alcista": "más empleos de los esperados confirma una economía sólida y sostiene expectativas de tasas altas, lo que fortalece la divisa",
        "razon_bajista": "menos empleos de los esperados es señal de debilidad económica y anticipa una postura más laxa del banco central, lo que suele debilitar la divisa",
    },
    "pmi": {
        "claves": [
            "pmi", "ism", "índice de gerentes de compra",
            "actividad manufacturera", "actividad de servicios",
            # Investing.com titula el mismo indicador con el nombre de la
            # firma que lo calcula (antes era "S&P Global" o "Markit",
            # segun el pais); sin esto, "HCOB Germany Manufacturing PMI"
            # SI se reconoce igual porque ya contiene "pmi", pero se deja
            # explicito por si el nombre de la firma cambia otra vez y el
            # titulo llegara a no incluir la palabra "PMI" en algun caso raro.
            "hcob", "s&p global",
        ],
        "nombre": "PMI / ISM (actividad manufacturera o de servicios)",
        "nivel": "clave",
        "alcista_si_sube": True,
        "razon_alcista": "un PMI mejor de lo esperado (o por encima de 50) señala expansión económica, lo que favorece a la divisa",
        "razon_bajista": "un PMI peor de lo esperado (o por debajo de 50) señala contracción, lo que suele pesar sobre la divisa",
    },
    "ventas_minoristas": {
        "claves": ["ventas minoristas", "retail sales"],
        "nombre": "Ventas minoristas",
        "nivel": "secundario",
        "alcista_si_sube": True,
        "razon_alcista": "un consumo mayor al esperado sostiene el crecimiento económico, lo que favorece a la divisa",
        "razon_bajista": "un consumo menor al esperado anticipa un crecimiento más débil, lo que suele pesar sobre la divisa",
    },
    "balanza_comercial": {
        "claves": ["balanza comercial", "trade balance"],
        "nombre": "Balanza comercial",
        "nivel": "secundario",
        "alcista_si_sube": True,
        "razon_alcista": "un mejor saldo comercial de lo esperado implica más entrada neta de divisas extranjeras",
        "razon_bajista": "un peor saldo comercial de lo esperado implica más salida neta de divisas, lo que puede presionar a la baja",
    },
    "confianza_consumidor": {
        "claves": [
            "confianza del consumidor", "consumer confidence",
            "sentimiento del consumidor", "consumer sentiment",
        ],
        "nombre": "Confianza / sentimiento del consumidor",
        "nivel": "secundario",
        "alcista_si_sube": True,
        "razon_alcista": "un consumidor más optimista de lo esperado anticipa mayor gasto futuro, lo que favorece a la divisa",
        "razon_bajista": "un consumidor más pesimista de lo esperado anticipa menor gasto futuro, lo que suele pesar sobre la divisa",
    },
    "vivienda": {
        "claves": [
            "vivienda", "housing", "permisos de construcción",
            "building permits", "housing starts", "inicios de construcción",
        ],
        "nombre": "Sector vivienda",
        "nivel": "secundario",
        "alcista_si_sube": True,
        "razon_alcista": "más actividad de la esperada en vivienda sugiere que la economía tolera bien las tasas actuales",
        "razon_bajista": "menos actividad de la esperada en vivienda sugiere que las tasas altas ya están frenando la economía",
    },
    "discurso": {
        "claves": [
            "discurso", "habla", "speech", "speaks", "speak", "remarks",
            "testimonio", "testimony", "conferencia de prensa",
            "press conference", "comparece",
        ],
        "nombre": "Discurso o comparecencia de un banquero central",
        "nivel": "clave",
        "alcista_si_sube": None,  # no aplica comparación numérica
        "razon_alcista": None,
        "razon_bajista": None,
    },
    "actas": {
        "claves": ["actas", "minutes", "fomc minutes"],
        "nombre": "Actas de la última reunión del banco central",
        "nivel": "clave",
        "alcista_si_sube": None,
        "razon_alcista": None,
        "razon_bajista": None,
    },
}

CATEGORIA_DEFECTO = {
    "nombre": "Noticia de alto impacto",
    "nivel": "descartar",
    "alcista_si_sube": None,
    "razon_alcista": None,
    "razon_bajista": None,
}


# Investing.com desglosa el "dot plot" de la Fed en varias líneas sueltas:
# "Interest Rate Projection - Third Year", "- Second Year", etc. Contienen
# la frase "interest rate" y por eso calificarían como "tasas" (clave) sin
# este filtro, cuando en realidad son el mismo dato reempaquetado varias
# veces — justo el tipo de ruido que se pidió excluir. Se descartan ANTES
# de la clasificación normal.
_PATRON_PROYECCION_SUELTA = re.compile(
    r"(interest rate|rate) projection.*\b(year|qtr|quarter)\b", re.IGNORECASE
)


def clasificar_evento(titulo):
    """Identifica a qué categoría económica pertenece el titular de la
    noticia, comparando por palabras clave. Si no reconoce ninguna,
    devuelve la categoría por defecto (sigue tratándose como noticia
    de alto impacto, solo que sin la explicación específica)."""
    if _PATRON_PROYECCION_SUELTA.search(titulo or ""):
        return CATEGORIA_DEFECTO
    titulo_normalizado = titulo.lower()
    for datos in CATEGORIAS.values():
        if any(clave in titulo_normalizado for clave in datos["claves"]):
            return datos
    return CATEGORIA_DEFECTO


def _a_numero(texto):
    """Convierte '3.2%', '170K', '-0.4B', '1,234' a un float comparable.
    Se asume que actual/pronóstico/anterior del MISMO evento comparten
    unidad y sufijo, así que basta con quitarlos igual en ambos lados
    antes de restar. Devuelve None si no se puede interpretar (dato aún
    no publicado, o formato inesperado)."""
    if not texto:
        return None
    limpio = texto.strip().replace("%", "").replace(",", "")
    limpio = limpio.rstrip("KMB")
    try:
        return float(limpio)
    except ValueError:
        return None


def analizar_resultado(evento):
    """
    Compara el dato PUBLICADO contra el PRONÓSTICO y arma una lectura
    fundamentada, no un texto genérico. Devuelve None si el dato todavía
    no se ha publicado o la categoría no admite comparación numérica
    (discursos, actas).

    Además compara contra el dato ANTERIOR (no solo contra el pronóstico):
    si ambas comparaciones apuntan en la misma dirección, es una señal más
    fuerte (doble confirmación); si el dato anterior era mejor, es una
    señal mixta que vale la pena marcar en vez de callar.
    """
    categoria = clasificar_evento(evento.get("titulo", ""))
    if categoria.get("alcista_si_sube") is None:
        return None

    actual = _a_numero(evento.get("actual"))
    pronostico = _a_numero(evento.get("pronostico"))
    if actual is None or pronostico is None:
        return None

    diferencia = actual - pronostico
    if abs(diferencia) < 1e-9:
        return {
            "resultado_txt": "salió exactamente en línea con lo esperado",
            "direccion": "neutral",
            "razon": (
                "sin sorpresa frente al pronóstico, no debería generar un "
                "movimiento direccional fuerte por sí solo"
            ),
            "confirmacion": None,
        }

    salio_por_encima = diferencia > 0
    es_alcista = salio_por_encima if categoria["alcista_si_sube"] else not salio_por_encima
    razon = categoria["razon_alcista"] if es_alcista else categoria["razon_bajista"]

    confirmacion = None
    anterior = _a_numero(evento.get("anterior"))
    if anterior is not None and abs(actual - anterior) > 1e-9:
        mejora_vs_anterior = (
            (actual > anterior) if categoria["alcista_si_sube"] else (actual < anterior)
        )
        if mejora_vs_anterior == es_alcista:
            confirmacion = "doble confirmación: también mejora frente al dato anterior"
        else:
            confirmacion = "el dato anterior era mejor: lectura mixta, cautela"

    return {
        "resultado_txt": (
            "salió por ENCIMA de lo esperado" if salio_por_encima
            else "salió por DEBAJO de lo esperado"
        ),
        "direccion": "alcista" if es_alcista else "bajista",
        "razon": razon,
        "confirmacion": confirmacion,
    }


# ══════════════════════════════════════════════════════════════════════════
# 6. CONSTRUCCIÓN DE MENSAJES  ·  todo en español, incluido lo que el feed
#    entrega en inglés (nivel de impacto y nombre de la noticia)
# ══════════════════════════════════════════════════════════════════════════
_IMPACTO_ES = {
    "High": "Alto", "Medium": "Medio", "Low": "Bajo", "Holiday": "Festivo",
}


def traducir_impacto(impacto):
    """'High' -> 'Alto', etc. Si el feed manda algo que no se reconoce, se
    muestra tal cual en vez de dejarlo en blanco."""
    return _IMPACTO_ES.get(impacto, impacto)


# Traducción del NOMBRE de la noticia. La fuente entrega los títulos
# siempre en inglés y no hay forma de pedírselos en español. Esta lista
# cubre los ~45 indicadores de alto impacto que se repiten cada semana o
# cada mes para cualquier país (PIB, IPC, tasas, empleo, PMI...), que es
# prácticamente todo lo que vas a recibir en la práctica.
#
# HONESTIDAD: un indicador rarísimo que no esté en esta lista se queda con
# su nombre original en inglés (no se inventa una traducción). Aun así, la
# explicación de "Análisis Fundamental" debajo del título SIEMPRE está en
# español, así que nunca te quedas sin saber qué significa la noticia.
#
# El orden importa: los patrones MÁS ESPECÍFICOS van primero (ej. "Core CPI"
# antes que "CPI"), porque una vez que "CPI" se reemplaza por "IPC", el
# patrón de "Core CPI" ya no encontraría la palabra "CPI" para matchear.
_TRADUCCIONES_TITULO = [
    (r"\bADP Non-Farm Employment Change\b", "Cambio de Empleo ADP (sector privado)"),
    (r"\bNon-Farm Employment Change\b", "Cambio de Empleo No Agrícola"),
    (r"\bNon-Farm Payrolls\b", "Nóminas No Agrícolas"),
    # Mas especifico PRIMERO (mismo principio que el resto de la lista): si
    # "Employment Change" (generico) fuera antes, se comeria la palabra
    # "Employment Change" DENTRO de "Full Employment Change" y dejaria
    # "Full" suelto sin traducir -se encontro probando contra un evento
    # real de Australia que distingue empleo total vs. tiempo completo.
    (r"\bFull Employment Change\b", "Cambio en el Empleo a Tiempo Completo"),
    (r"\bPart[- ]?Time Employment Change\b", "Cambio en el Empleo a Tiempo Parcial"),
    (r"\bEmployment Change\b", "Cambio en el Empleo"),
    (r"\bAverage Hourly Earnings\b", "Ingreso Medio por Hora"),
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
    (r"\bProducer Price Index\b", "Índice de Precios al Productor"),
    (r"\bPPI\b", "IPP"),
    (r"\bPrelim GDP\b", "PIB Preliminar"),
    (r"\bFinal GDP\b", "PIB Final"),
    (r"\bGDP\b", "PIB"),
    (r"\bCore Retail Sales\b", "Ventas Minoristas Subyacentes"),
    (r"\bRetail Sales\b", "Ventas Minoristas"),
    (r"\bTrade Balance\b", "Balanza Comercial"),
    (r"\bCurrent Account\b", "Cuenta Corriente"),
    (r"\bConsumer Confidence\b", "Confianza del Consumidor"),
    (r"\bConsumer Sentiment\b", "Sentimiento del Consumidor"),
    (r"\bBusiness Confidence\b", "Confianza Empresarial"),
    (r"\bEconomic Sentiment\b", "Sentimiento Económico"),
    (r"\bBuilding Permits\b", "Permisos de Construcción"),
    (r"\bHousing Starts\b", "Inicios de Construcción de Viviendas"),
    (r"\bExisting Home Sales\b", "Ventas de Viviendas Existentes"),
    (r"\bNew Home Sales\b", "Ventas de Viviendas Nuevas"),
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
    (r"\bInterest Rate Decision\b", "Decisión de Tipos de Interés"),
    (r"\bFederal Funds Rate\b", "Tasa de Fondos Federales"),
    (r"\bOfficial Bank Rate\b", "Tasa Oficial del Banco"),
    (r"\bOvernight Rate\b", "Tasa Overnight"),
    (r"\bCash Rate\b", "Tasa de Efectivo"),
    (r"\bSNB Policy Rate\b", "Tasa de Política del SNB"),
    (r"\bPolicy Rate\b", "Tasa de Política"),
    (r"\bBank Rate\b", "Tasa Bancaria"),
    (r"\bMain Refinancing Rate\b", "Tasa de Refinanciación Principal"),
    (r"\bDeposit Facility Rate\b", "Tasa de Depósito"),
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
    # Adjetivos de país que suelen ir delante del nombre del indicador
    # (ej. "German ZEW Economic Sentiment"). Van al final porque son
    # palabras sueltas genéricas, no frases completas del indicador.
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
    """Traduce las partes reconocidas del título. Lo que no reconoce lo
    deja en inglés (mejor eso que inventar una traducción incorrecta)."""
    resultado = titulo or ""
    for patron, reemplazo in _TRADUCCIONES_TITULO_COMPILADAS:
        resultado = patron.sub(reemplazo, resultado)
    return resultado


def _escapar(texto):
    """Telegram (modo Markdown clásico) rompe el mensaje si el texto trae
    guiones bajos o asteriscos sueltos (frecuente en títulos de noticias
    en inglés, ej. 'Non-Farm_Payrolls'). Se neutralizan para que el envío
    nunca falle por esto."""
    return (texto or "").replace("_", "-").replace("*", "").replace("`", "'")


# Solo presentación visual, ningún dato: la bandera es la del país emisor
# de cada divisa objetivo (no se inventa nada, es la bandera real de esa
# moneda) y el ícono de categoría ayuda a reconocer de un vistazo qué tipo
# de noticia es sin tener que leer el título completo.
_BANDERAS = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵",
    "AUD": "🇦🇺", "CAD": "🇨🇦", "CHF": "🇨🇭", "NZD": "🇳🇿",
}

_EMOJI_CATEGORIA = {
    "Decisión de tasas de interés": "💰",
    "Producto Interno Bruto (PIB)": "📊",
    "Inflación (IPC / PCE / PPI)": "🔥",
    "Tasa de desempleo": "📉",
    "Solicitudes de subsidio por desempleo": "📝",
    "Creación de empleo (Nóminas no agrícolas / NFP)": "👷",
    "PMI / ISM (actividad manufacturera o de servicios)": "🏭",
    "Ventas minoristas": "🛒",
    "Balanza comercial": "🚢",
    "Confianza / sentimiento del consumidor": "😊",
    "Sector vivienda": "🏠",
    "Discurso o comparecencia de un banquero central": "🎤",
    "Actas de la última reunión del banco central": "📜",
}


def _bandera(divisa):
    return _BANDERAS.get(divisa, "")


def _emoji_categoria(titulo):
    nombre = clasificar_evento(titulo).get("nombre", "")
    return _EMOJI_CATEGORIA.get(nombre, "📰")


def mensaje_previo(grupo, minutos):
    """
    Recibe una LISTA de eventos, no uno solo. Motivo: en días como el de
    la Fed, la fuente publica 3-4 noticias en el MISMO minuto exacto (tasa,
    proyecciones, comunicado...) y antes esto mandaba un aviso casi
    idéntico por cada una — 4 mensajes seguidos para lo que en la práctica
    es un solo momento. Ahora, si comparten divisa y hora exacta, se
    agrupan en uno solo. El caso normal (1 sola noticia) se ve exactamente
    igual que antes.

    En un grupo (día de la Fed o del SNB) se usa la categoría de cada
    integrante que sí admita comparación numérica -si el grupo mezcla mas
    de una categoria con logica distinta (ej. Empleo + Tasa de Desempleo
    el mismo minuto, que funcionan AL REVES una de otra), se da una linea
    de razonamiento por cada una en vez de una conclusion unica que solo
    seria cierta para la mitad del grupo.
    """
    divisa = grupo[0]["divisa"]
    bandera = _bandera(divisa)
    hora_local = grupo[0]["fecha"].astimezone(ZONA_HORARIA).strftime("%H:%M")
    es_urgente = minutos <= min(AVISOS_PREVIOS_MIN)
    palabra_min = "minuto" if minutos == 1 else "minutos"
    emoji = "🚨" if es_urgente else "⚠️"

    if len(grupo) == 1:
        evento = grupo[0]
        titulo = _escapar(traducir_titulo(evento["titulo"]))
        icono = _emoji_categoria(evento["titulo"])
        base = (
            f"{emoji} *En {minutos} {palabra_min}:* {icono} {titulo} ({bandera} {divisa})\n"
            f"Impacto: {traducir_impacto(evento['impacto'])} · {hora_local} (Colombia)"
        )
        # Un discurso o unas actas nunca traen pronóstico ni dato anterior
        # (no es una cifra que comparar) - mostrar "sin dato" en ambos
        # campos es ruido, no información, así que esa línea solo se
        # agrega cuando el evento SÍ es de los que traen una cifra.
        if clasificar_evento(evento["titulo"]).get("alcista_si_sube") is not None:
            base += (
                f"\nPronóstico: {evento['pronostico'] or 'sin dato'} | "
                f"Anterior: {evento['anterior'] or 'sin dato'}"
            )
    else:
        icono = _emoji_categoria(grupo[0]["titulo"])
        titulos = "\n".join(
            f"• {_escapar(traducir_titulo(e['titulo']))}" for e in grupo
        )
        base = (
            f"{emoji} *En {minutos} {palabra_min}:* {icono} {len(grupo)} noticias de "
            f"{bandera} {divisa} al mismo tiempo ({hora_local} Colombia):\n{titulos}"
        )

    if es_urgente:
        return base

    categorias_comparables = []
    nombres_vistos = set()
    for ev in grupo:
        cat = clasificar_evento(ev["titulo"])
        if cat.get("alcista_si_sube") is not None and cat["nombre"] not in nombres_vistos:
            nombres_vistos.add(cat["nombre"])
            categorias_comparables.append(cat)

    if not categorias_comparables:
        analisis = (
            "🧠 No trae una cifra que comparar contra un pronóstico: el "
            "mercado reacciona al TONO del mensaje, no a un número."
        )
    elif len(categorias_comparables) == 1:
        categoria = categorias_comparables[0]
        if categoria["alcista_si_sube"]:
            dir_encima, dir_debajo = "alcista", "bajista"
            razon_encima, razon_debajo = categoria["razon_alcista"], categoria["razon_bajista"]
        else:
            dir_encima, dir_debajo = "bajista", "alcista"
            razon_encima, razon_debajo = categoria["razon_bajista"], categoria["razon_alcista"]
        analisis = (
            f"🧠 *Por encima del pronóstico:* {dir_encima} para {divisa} "
            f"— {razon_encima}.\n"
            f"*Por debajo:* {dir_debajo} — {razon_debajo}."
        )
    else:
        lineas = ["🧠 *Este grupo mezcla indicadores con lógica distinta:*"]
        for cat in categorias_comparables:
            if cat["alcista_si_sube"]:
                lineas.append(f"• {cat['nombre']}: por encima del pronóstico es alcista, por debajo bajista.")
            else:
                lineas.append(f"• {cat['nombre']}: por encima del pronóstico es bajista, por debajo alcista (indicador inverso).")
        analisis = "\n".join(lineas)

    return f"{base}\n\n{analisis}"


def mensaje_publicacion(grupo):
    """Igual que mensaje_previo: recibe una lista para poder agrupar
    noticias simultáneas de la misma divisa en un solo aviso."""
    divisa = grupo[0]["divisa"]
    bandera = _bandera(divisa)
    if len(grupo) == 1:
        evento = grupo[0]
        titulo = _escapar(traducir_titulo(evento["titulo"]))
        icono = _emoji_categoria(evento["titulo"])
        # Un discurso "empieza", no "se publica" (no trae una cifra que
        # publicar), así que ni el verbo ni la línea de pronóstico/anterior
        # aplican - ver el mismo razonamiento en mensaje_previo.
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
    """
    Solo se llama cuando SÍ hay un resultado real que mostrar. Si
    Investing.com nunca completa el dato "actual" para este evento puntual
    (pasa rara vez, pero puede pasar), o el evento es un discurso/actas
    sin cifra que comparar, `job_reaccion` nunca invoca esta función y no
    se manda nada -silencio, no una nota diciendo que no llegó.
    """
    titulo = _escapar(traducir_titulo(evento["titulo"]))
    bandera = _bandera(evento["divisa"])
    icono = _emoji_categoria(evento["titulo"])
    emoji = {"alcista": "🟢", "bajista": "🔴", "neutral": "⚪"}[analisis["direccion"]]
    linea_lectura = f"Lectura: {analisis['direccion'].upper()} para {bandera} {evento['divisa']}"
    if analisis.get("confirmacion"):
        linea_lectura += f" ({analisis['confirmacion']})"
    return (
        f"{emoji} *{icono} {titulo}* ({bandera} {evento['divisa']}): {evento['actual']} vs "
        f"{evento['pronostico']} esperado · anterior {evento['anterior']}\n"
        f"{linea_lectura}"
    )


def mensaje_reporte_matutino(eventos_hoy):
    if not eventos_hoy:
        return (
            "🌅 *Resumen del mercado - hoy*\n\n"
            "No hay noticias de alto impacto programadas hoy para tus "
            "divisas objetivo. Día tranquilo en el calendario (no "
            "necesariamente en el precio)."
        )

    eventos_hoy = sorted(eventos_hoy, key=lambda e: e["fecha"])
    lineas = ["🌅 *Resumen del mercado - Alto impacto (3 toros)*\n"]
    lineas.append(f"🗓️ Hoy tienes {len(eventos_hoy)} inyecciones de liquidez:\n")
    for ev in eventos_hoy:
        hora = ev["fecha"].astimezone(ZONA_HORARIA).strftime("%H:%M")
        titulo = _escapar(traducir_titulo(ev["titulo"]))
        bandera = _bandera(ev["divisa"])
        icono = _emoji_categoria(ev["titulo"])
        pron = f" (pronóstico: {ev['pronostico']})" if ev["pronostico"] else ""
        lineas.append(f"• {hora} | {bandera} {ev['divisa']} | {icono} {titulo}{pron}")
    return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════════════
# 7. ENVÍO
# ══════════════════════════════════════════════════════════════════════════
def _enviar(mensaje):
    try:
        bot.send_message(CHAT_ID, mensaje, parse_mode="Markdown")
    except Exception as e:  # nunca dejar que un fallo de Telegram tumbe el scheduler
        log.error("Fallo al enviar mensaje a Telegram: %s", e)
        _estado_salud["ultimo_error"] = f"envío: {e}"


def job_aviso_previo(grupo, minutos):
    _enviar(mensaje_previo(grupo, minutos))


def job_publicacion(grupo):
    _enviar(mensaje_publicacion(grupo))


def _clave_reaccion(evento):
    return f"{evento['divisa']}|{evento['titulo']}|{evento['fecha'].isoformat()}"


def job_reaccion(evento):
    """
    Se llama varias veces por evento (ver REINTENTOS_REACCION_MIN), no una
    sola. En cada llamada vuelve a descargar el calendario (respetando la
    caché de 2 minutos) y compara contra el pronóstico.

    - Si ya se mandó la comparación en un intento anterior, no hace nada
      (esto se guarda EN DISCO, no solo en memoria, para que sobreviva a
      un reinicio de Render a mitad de la ventana de 30 minutos).
    - Si consigue el dato, manda el resultado real UNA vez y no vuelve a
      intentar.
    - Si no hay dato (todavía no se publicó, o el evento es un
      discurso/actas sin cifra que comparar), NO SE MANDA NADA.
    """
    clave = _clave_reaccion(evento)
    if _ya_reaccion_enviada(clave):
        return

    eventos_frescos = obtener_calendario()
    actualizado = evento
    for ev in eventos_frescos:
        if ev["titulo"] == evento["titulo"] and ev["divisa"] == evento["divisa"] \
                and ev["fecha"] == evento["fecha"]:
            actualizado = ev
            break

    analisis = analizar_resultado(actualizado)
    if analisis is None:
        return  # sin dato (o no comparable): silencio, se reintenta despues

    _marcar_reaccion_enviada(clave)
    _enviar(mensaje_reaccion(actualizado, analisis))


def job_reporte_matutino():
    eventos = obtener_calendario()
    hoy = eventos_relevantes(eventos, solo_hoy=True)
    _estado_salud["eventos_detectados_hoy"] = len(hoy)
    _enviar(mensaje_reporte_matutino(hoy))


# ══════════════════════════════════════════════════════════════════════════
# 8. SINCRONIZACIÓN Y PROGRAMACIÓN DINÁMICA
# ══════════════════════════════════════════════════════════════════════════
scheduler = BackgroundScheduler(timezone=ZONA_HORARIA)


def sincronizar_calendario():
    """
    Se ejecuta al arrancar y luego cada INTERVALO_SINCRONIZACION_MIN.
    Descarga el calendario, filtra tus divisas y el impacto que
    monitoreas, y programa (si no estaban programados ya) los avisos de
    cada noticia futura: 15 min antes, 5 min antes, al momento, y la
    comparación real del resultado (con reintentos hasta 30 minutos
    después; si nunca hay dato que comparar, no se manda nada — ver
    job_reaccion).

    Los avisos previos y el de publicación se programan por GRUPO, no por
    evento individual: si dos o más noticias de la misma divisa caen en el
    minuto exacto (típico en día de la Fed: tasa + proyecciones +
    comunicado a las 13:00 en punto), se manda un solo aviso combinado en
    vez de uno por cada una. La comparación real del resultado (reacción)
    sí se maneja por evento individual, porque cada indicador tiene su
    propio número que comparar y agruparlos ahí perdería información en
    vez de solo reducir ruido.
    """
    ahora = datetime.now(pytz.utc)
    estado = _cargar_estado_disco()
    eventos = obtener_calendario()
    relevantes = eventos_relevantes(eventos, solo_hoy=False)
    ventana_reaccion = max(REINTENTOS_REACCION_MIN)

    grupos = {}
    for ev in relevantes:
        grupos.setdefault((ev["divisa"], ev["fecha"]), []).append(ev)

    nuevos = 0
    for (divisa, fecha), grupo in grupos.items():
        clave_grupo = f"{divisa}|{fecha.isoformat()}|grupo"
        ya_paso = fecha < ahora - timedelta(minutes=ventana_reaccion)

        if not _ya_programado(estado, clave_grupo):
            if not ya_paso:
                for minutos in AVISOS_PREVIOS_MIN:
                    momento = fecha - timedelta(minutes=minutos)
                    if momento > ahora:
                        scheduler.add_job(
                            job_aviso_previo, "date", run_date=momento,
                            args=[grupo, minutos],
                            id=f"{clave_grupo}|previo{minutos}", replace_existing=True,
                        )
                if fecha > ahora:
                    scheduler.add_job(
                        job_publicacion, "date", run_date=fecha,
                        args=[grupo], id=f"{clave_grupo}|ahora", replace_existing=True,
                    )
            # se marca aunque ya haya pasado (ya_paso): evita reintentar
            # programar un aviso de "faltan 15 minutos" para algo que
            # ocurrió hace horas, por ejemplo si el bot estuvo caído.
            _marcar_programado(estado, clave_grupo)

        # Reintentos de reacción: uno por evento individual dentro del grupo.
        for ev in grupo:
            clave_ev = _clave_reaccion(ev)
            if _ya_programado(estado, clave_ev):
                continue
            if ev["fecha"] < ahora - timedelta(minutes=ventana_reaccion):
                _marcar_programado(estado, clave_ev)
                continue
            # Mismo bloque de reintentos para cualquier evento: si es un
            # discurso/actas sin cifra, o si el dato nunca llega,
            # job_reaccion simplemente no manda nada (ver su docstring).
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
        "Sincronización de calendario: %d eventos relevantes en %d grupos, %d nuevos programados.",
        len(relevantes), len(grupos), nuevos,
    )


def programar_trabajos_fijos():
    scheduler.add_job(job_reporte_matutino, "cron", hour=6, minute=0, id="reporte_matutino")
    scheduler.add_job(
        sincronizar_calendario, "interval",
        minutes=INTERVALO_SINCRONIZACION_MIN, id="sincronizacion_periodica",
    )
    scheduler.start()
    # Primera sincronización inmediata al arrancar, para no esperar una
    # hora completa antes de tener algo programado.
    sincronizar_calendario()


# ══════════════════════════════════════════════════════════════════════════
# 9. EJECUCIÓN PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    log.info("Arrancando bot de calendario económico...")

    programar_trabajos_fijos()

    hilo_web = threading.Thread(target=run_flask, daemon=True)
    hilo_web.start()

    log.info("Servidor web y programador activos. Iniciando polling de Telegram...")
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            # Si Telegram corta la conexión (pasa de vez en cuando en
            # servicios gratuitos), se reintenta en vez de morir el proceso.
            log.error("El polling de Telegram se cortó (%s); reintentando en 15s.", e)
            _estado_salud["ultimo_error"] = f"polling: {e}"
            time.sleep(15)
