#!/usr/bin/env python3
"""
Bot de Telegram: noticias de ORO, BITCOIN y PETRÓLEO en (casi) tiempo real.

- Lee feeds RSS de fuentes fiables (bancos centrales, agencias, prensa financiera).
- Filtra por palabras clave de cada activo y por eventos macro de alto impacto.
- Evita duplicados (mismo enlace o titulares casi idénticos de distintas fuentes).
- Envía las noticias a tu chat de Telegram.

Modos:
    python bot.py                 # bucle continuo
    python bot.py --once          # una sola pasada y sale
    python bot.py --test-feeds    # comprueba qué feeds responden
    python bot.py --test-telegram # envía un mensaje de prueba
    python bot.py --chat-id       # muestra tu chat id (escribe antes al bot)
"""

import argparse
import difflib
import hashlib
import html
import logging
import os
import re
import sqlite3
import sys
import time
from calendar import timegm
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ----------------------------------------------------------------------------
# Configuración (se puede cambiar en .env)
# ----------------------------------------------------------------------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "120"))
MAX_AGE_HOURS = float(os.getenv("MAX_AGE_HOURS", "12"))
ONLY_HIGH_IMPACT = os.getenv("ONLY_HIGH_IMPACT", "0") == "1"
MAX_PER_CYCLE = int(os.getenv("MAX_PER_CYCLE", "15"))
# 1 = no enviar el mensaje "Bot activo" al arrancar (útil en GitHub Actions)
QUIET_START = os.getenv("QUIET_START", "0") == "1"
USE_OLLAMA = os.getenv("USE_OLLAMA", "0") == "1"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:14b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
DB_PATH = BASE_DIR / "seen.db"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ----------------------------------------------------------------------------
# Fuentes. tier 1 = fuente oficial, tier 2 = prensa/agencia financiera de referencia.
# Si alguna deja de funcionar, el bot lo registra y sigue con las demás.
# ----------------------------------------------------------------------------
FEEDS = [
    # Oficiales
    {"name": "Federal Reserve", "url": "https://www.federalreserve.gov/feeds/press_all.xml", "tier": 1},
    {"name": "EIA (energía EE. UU.)", "url": "https://www.eia.gov/rss/todayinenergy.xml", "tier": 1},
    # Prensa y agencias financieras
    {"name": "CNBC Finance", "url": "https://www.cnbc.com/id/10000664/device/rss/rss.html", "tier": 2},
    {"name": "CNBC Economy", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html", "tier": 2},
    {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories", "tier": 2},
    {"name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "tier": 2},
    # Especializadas
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "tier": 2},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss", "tier": 2},
    {"name": "OilPrice.com", "url": "https://oilprice.com/rss/main", "tier": 2},
    {"name": "Yahoo Finance · Oro", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F&region=US&lang=en-US", "tier": 2},
    {"name": "Yahoo Finance · Bitcoin", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD&region=US&lang=en-US", "tier": 2},
    {"name": "Yahoo Finance · Petróleo", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=CL=F&region=US&lang=en-US", "tier": 2},
]

# ----------------------------------------------------------------------------
# Palabras clave
# ----------------------------------------------------------------------------
ASSETS = {
    "oro": {
        "emoji": "🥇",
        "label": "Oro",
        "kw": [r"\bgold\b", r"\bbullion\b", r"\bxau\b", r"safe[- ]haven", r"\bkitco\b",
               r"central[- ]bank gold", r"gold reserves"],
    },
    "bitcoin": {
        "emoji": "₿",
        "label": "Bitcoin",
        "kw": [r"\bbitcoin\b", r"\bbtc\b", r"spot (bitcoin|crypto) etf", r"\bcrypto(currency|currencies)?\b",
               r"\bmicrostrategy\b", r"\bblackrock.*(btc|bitcoin)\b"],
    },
    "petroleo": {
        "emoji": "🛢",
        "label": "Petróleo",
        "kw": [r"\boil\b", r"\bcrude\b", r"\bbrent\b", r"\bwti\b", r"\bopec\+?\b",
               r"\brefiner(y|ies)\b", r"strait of hormuz", r"\bpetroleum\b", r"oil (prices?|supply|inventories)"],
    },
}

# Macro que mueve los tres activos
MACRO_KW = [
    r"\bfomc\b", r"federal reserve", r"\bthe fed\b", r"\bfed (chair|holds|cuts|raises|hikes|signals|minutes)\b",
    r"\bpowell\b", r"interest rates?", r"rate (cut|hike|decision)", r"\binflation\b", r"\bcpi\b", r"\bpce\b",
    r"nonfarm|jobs report|payrolls", r"treasury yields?", r"dollar index|\bdxy\b|u\.s\. dollar|us dollar",
    r"\btariffs?\b", r"\bsanctions?\b", r"\bceasefire\b", r"\bwar\b", r"\binvasion\b", r"\brecession\b",
]

# Eventos de ALTO impacto (marcan la noticia como 🔴)
HIGH_KW = [
    r"\bfomc\b", r"rate (cut|hike|decision)", r"\bcpi\b", r"\bpce\b", r"nonfarm|jobs report|payrolls",
    r"\bfed (holds|cuts|raises|hikes|lowers|keeps|signals)\b", r"\bpowell\b",
    r"\bopec\+?\b.*(cut|output|production|meeting|decision)", r"emergency", r"\bsanctions?\b",
    r"\bceasefire\b", r"\binvasion\b", r"\bwar\b", r"etf (approv|reject|inflow|outflow)",
    r"\b(sec|cftc)\b.*(crypto|bitcoin|etf|lawsuit|ban)", r"\bhack(ed)?\b|\bexploit\b", r"\bban(s|ned)?\b",
    r"record high|all[- ]time high|plunge|plummet|surge|soar|crash|tumble",
    r"strait of hormuz", r"central bank.*(buy|purchas|sell)", r"\bdefault\b",
]

ASSET_RE = {k: [re.compile(p, re.I) for p in v["kw"]] for k, v in ASSETS.items()}
MACRO_RE = [re.compile(p, re.I) for p in MACRO_KW]
HIGH_RE = [re.compile(p, re.I) for p in HIGH_KW]

log = logging.getLogger("newsbot")


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def setup_logging():
    fmt = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8")],
    )


def clean_text(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_title(t: str) -> str:
    t = re.sub(r"[^a-z0-9 ]", "", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def entry_id(entry) -> str:
    key = entry.get("link") or entry.get("id") or entry.get("title", "")
    return hashlib.sha1(key.encode("utf-8", "ignore")).hexdigest()


def entry_time(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return None
    return datetime.fromtimestamp(timegm(t), tz=timezone.utc)


def human_age(dt) -> str:
    if not dt:
        return ""
    mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if mins < 1:
        return "ahora"
    if mins < 60:
        return f"hace {mins} min"
    return f"hace {mins // 60} h"


# ----------------------------------------------------------------------------
# Clasificación
# ----------------------------------------------------------------------------
def classify(title: str, summary: str = ""):
    """Devuelve (activos, es_macro, impacto) o None si no es relevante."""
    text = f"{title}. {summary}"
    assets = [k for k, pats in ASSET_RE.items() if any(p.search(title) for p in pats)]
    # Si el titular no menciona el activo, miramos el resumen con criterio más estricto
    if not assets:
        assets = [k for k, pats in ASSET_RE.items()
                  if sum(1 for p in pats if p.search(summary)) >= 2]
    is_macro = any(p.search(title) for p in MACRO_RE)
    high = any(p.search(text) for p in HIGH_RE)

    if not assets and not is_macro:
        return None
    # Macro puro (sin mención directa a un activo): solo si es de alto impacto
    if not assets and is_macro and not high:
        return None
    return assets, (is_macro and not assets), ("alta" if high else "media")


# ----------------------------------------------------------------------------
# Base de datos (evita repetir noticias)
# ----------------------------------------------------------------------------
def db_connect():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, title TEXT, ts REAL)")
    return con


def db_is_empty(con) -> bool:
    return con.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0


def db_seen(con, eid: str) -> bool:
    return con.execute("SELECT 1 FROM seen WHERE id=?", (eid,)).fetchone() is not None


def db_mark(con, eid: str, title: str):
    con.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?)", (eid, norm_title(title), time.time()))
    con.commit()


def db_recent_titles(con, limit=300):
    rows = con.execute("SELECT title FROM seen ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [r[0] for r in rows]


def is_near_duplicate(title: str, recent: list) -> bool:
    nt = norm_title(title)
    for r in recent:
        if difflib.SequenceMatcher(None, nt, r).ratio() >= 0.82:
            return True
    return False


# ----------------------------------------------------------------------------
# Feeds
# ----------------------------------------------------------------------------
def fetch_feed(feed: dict):
    r = requests.get(feed["url"], headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    parsed = feedparser.parse(r.content)
    return parsed.entries


def collect_candidates():
    """Descarga todos los feeds y devuelve la lista de noticias relevantes."""
    out = []
    now = datetime.now(timezone.utc)
    for feed in FEEDS:
        try:
            entries = fetch_feed(feed)
        except Exception as e:  # un feed caído no debe parar el bot
            log.warning("Feed con error (%s): %s", feed["name"], e)
            continue
        for e in entries:
            title = clean_text(e.get("title", ""))
            if not title:
                continue
            dt = entry_time(e)
            if dt and (now - dt).total_seconds() > MAX_AGE_HOURS * 3600:
                continue
            summary = clean_text(e.get("summary", ""))[:600]
            cls = classify(title, summary)
            if not cls:
                continue
            assets, is_macro, impact = cls
            out.append({
                "id": entry_id(e), "title": title, "summary": summary,
                "link": e.get("link", ""), "source": feed["name"], "tier": feed["tier"],
                "time": dt, "assets": assets, "macro": is_macro, "impact": impact,
            })
    # Más antiguas primero, y a igualdad, fuentes oficiales antes
    out.sort(key=lambda x: (x["time"] or now, x["tier"]))
    return out


# ----------------------------------------------------------------------------
# Ollama opcional: resumen en español + sesgo
# ----------------------------------------------------------------------------
def ollama_analysis(item):
    labels = ", ".join(ASSETS[a]["label"] for a in item["assets"]) or "oro, bitcoin y petróleo"
    prompt = (
        "/no_think\n"
        "Eres un analista de mercados. Responde SOLO en español y en máximo 2 frases cortas: "
        "traduce la idea del titular y di si es alcista, bajista o neutral para "
        f"{labels}, con una razón breve. No inventes datos.\n\n"
        f"Titular: {item['title']}\nResumen: {item['summary'][:400]}"
    )
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "stream": False,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=120,
        )
        r.raise_for_status()
        txt = r.json()["message"]["content"]
        txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
        return txt[:400] or None
    except Exception as e:
        log.warning("Ollama no disponible: %s", e)
        return None


# ----------------------------------------------------------------------------
# Telegram
# ----------------------------------------------------------------------------
def tg_api(method: str, **payload):
    """Llama a la API de Telegram. Los errores NUNCA incluyen la URL (lleva el token)."""
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    for _ in range(3):
        try:
            r = requests.post(url, json=payload, timeout=30)
        except requests.RequestException as e:
            raise RuntimeError(f"Telegram: error de red ({type(e).__name__})") from None
        if r.status_code == 429:
            try:
                wait = r.json().get("parameters", {}).get("retry_after", 5)
            except ValueError:
                wait = 5
            time.sleep(wait + 1)
            continue
        if not r.ok:
            try:
                desc = r.json().get("description", r.text[:200])
            except ValueError:
                desc = r.text[:200]
            raise RuntimeError(f"Telegram {method}: HTTP {r.status_code} - {desc}") from None
        return r.json()
    raise RuntimeError("Telegram: demasiados reintentos")


def format_message(item, analysis=None) -> str:
    tags = " · ".join(f"{ASSETS[a]['emoji']} {ASSETS[a]['label']}" for a in item["assets"])
    if item["macro"]:
        tags = "🌐 Macro (afecta a oro/BTC/petróleo)"
    flag = "🔴 ALTO IMPACTO" if item["impact"] == "alta" else "🟡 Relevante"
    lines = [
        f"{flag} · {tags}",
        f"<b>{html.escape(item['title'])}</b>",
    ]
    if analysis:
        lines.append(f"🤖 {html.escape(analysis)}")
    meta = f"{html.escape(item['source'])}"
    age = human_age(item["time"])
    if age:
        meta += f" · {age}"
    lines.append(meta)
    if item["link"]:
        lines.append(html.escape(item["link"]))
    return "\n".join(lines)


def send_text(text: str):
    tg_api("sendMessage", chat_id=CHAT_ID, text=text, parse_mode="HTML",
           disable_web_page_preview=True)


# ----------------------------------------------------------------------------
# Ciclo principal
# ----------------------------------------------------------------------------
def run_cycle(con, bootstrap=False) -> int:
    candidates = collect_candidates()
    recent = db_recent_titles(con)
    sent = 0
    for item in candidates:
        if db_seen(con, item["id"]):
            continue
        if bootstrap:
            db_mark(con, item["id"], item["title"])
            continue
        if is_near_duplicate(item["title"], recent):
            db_mark(con, item["id"], item["title"])
            continue
        if ONLY_HIGH_IMPACT and item["impact"] != "alta":
            db_mark(con, item["id"], item["title"])
            continue
        if sent >= MAX_PER_CYCLE:
            break  # el resto se enviará en el siguiente ciclo
        analysis = ollama_analysis(item) if USE_OLLAMA else None
        try:
            send_text(format_message(item, analysis))
        except Exception as e:
            log.error("No se pudo enviar a Telegram: %s", e)
            break  # no marcamos como visto: se reintentará
        db_mark(con, item["id"], item["title"])
        recent.insert(0, norm_title(item["title"]))
        sent += 1
        time.sleep(1.2)  # respeta límites de Telegram
    log.info("Ciclo terminado: %d candidatas, %d enviadas", len(candidates), sent)
    return sent


def require_telegram(need_chat=True):
    if not TOKEN:
        sys.exit("Falta TELEGRAM_BOT_TOKEN en el archivo .env")
    if need_chat and not CHAT_ID:
        sys.exit("Falta TELEGRAM_CHAT_ID en .env. Escribe a tu bot y ejecuta: python bot.py --chat-id")


def cmd_test_feeds():
    ok = 0
    for f in FEEDS:
        try:
            n = len(fetch_feed(f))
            print(f"OK    {f['name']:<28} {n} entradas")
            ok += 1
        except Exception as e:
            print(f"ERROR {f['name']:<28} {e}")
    print(f"\n{ok}/{len(FEEDS)} feeds funcionan.")


def cmd_chat_id():
    require_telegram(need_chat=False)
    data = tg_api("getUpdates")
    seen = {}
    for u in data.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            seen[chat["id"]] = chat.get("username") or chat.get("first_name") or chat.get("title")
    if not seen:
        print("No hay mensajes. Abre tu bot en Telegram, pulsa Iniciar, escribe 'hola' y repite.")
    for cid, name in seen.items():
        print(f"TELEGRAM_CHAT_ID={cid}   ({name})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--test-feeds", action="store_true")
    ap.add_argument("--test-telegram", action="store_true")
    ap.add_argument("--chat-id", action="store_true")
    args = ap.parse_args()

    setup_logging()

    if args.test_feeds:
        return cmd_test_feeds()
    if args.chat_id:
        return cmd_chat_id()
    require_telegram()
    if args.test_telegram:
        send_text("✅ Bot de noticias conectado correctamente.")
        print("Mensaje enviado.")
        return

    con = db_connect()
    first_run = db_is_empty(con)
    if first_run:
        log.info("Primera ejecución: marco las noticias actuales como vistas (sin enviarlas).")
        run_cycle(con, bootstrap=True)
        if not QUIET_START:
            send_text("✅ Bot activo. Vigilando noticias de 🥇 oro, ₿ bitcoin y 🛢 petróleo.")
    if args.once:
        run_cycle(con)
        return

    log.info("Bot en marcha. Revisando cada %d s. Ctrl+C para parar.", POLL_SECONDS)
    while True:
        try:
            run_cycle(con)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.exception("Error en el ciclo: %s", e)
        try:
            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            break
    log.info("Bot detenido.")


if __name__ == "__main__":
    main()
