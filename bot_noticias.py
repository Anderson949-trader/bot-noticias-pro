"""Bot de Telegram para calendario económico y alertas Forex.

Configura TELEGRAM_TOKEN y TELEGRAM_CHAT_ID mediante variables de entorno.
La lectura fundamental es orientativa y no constituye una señal automática.
"""
import os
import re
import json
import time
import logging
import threading
import unicodedata
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from dotenv import load_dotenv

import pytz
import telebot
from telebot import types
from bs4 import BeautifulSoup
from flask import Flask
from waitress import serve
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()  # Permite usar .env local sin subirlo a GitHub.

# ══════════════════════════════════════════════════════════════════════════
# 1. CONFIGURACIÓN GENERAL
# ══════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("bot_forex")

# No se incluyen credenciales por defecto. Si falta algo, el proceso falla
# al arrancar para evitar que el bot quede silenciosamente mal configurado.
TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not TOKEN:
    raise SystemExit("Falta TELEGRAM_TOKEN. Configúralo como variable de entorno.")
if not CHAT_ID or not re.fullmatch(r"-?\d+", CHAT_ID):
    raise SystemExit("TELEGRAM_CHAT_ID debe ser un ID numérico de Telegram.")

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
