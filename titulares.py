#!/usr/bin/env python3
"""
Extrae el titular principal de los medios digitales españoles de medios.json.

1. Playwright visita cada portada y saca sus candidatos a titular (enlaces
   visibles de la parte alta, ordenados por tamaño de letra). El País bloquea
   headless a nivel de red (Akamai), así que sus candidatos salen de su RSS.
2. clasificador.py pide a Claude que elija el titular principal de cada medio
   y lo catalogue (temática e historia compartida entre medios).
   Sin API key o si la llamada falla, se usa el primer candidato (heurística).

Genera titulares.json (lista de { media, headline, mediaUrl, ... }) y una copia
en historico/AAAA/MM/.
"""
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from clasificador import clasificar

OUTPUT_FILE = "titulares.json"
HISTORICO_DIR = Path("historico")

MEDIOS_FILE = "medios.json"
TIMEOUT = 20000
LOGO_MAX_LEN = 30
MAX_CANDIDATOS = 30
MAX_CANDIDATOS_RSS = 10
MADRID = ZoneInfo("Europe/Madrid")

CONTEXT_OPTS = {
    "user_agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "viewport": {"width": 1920, "height": 1080},
    "device_scale_factor": 2,
    "locale": "es-ES",
    "timezone_id": "Europe/Madrid",
    "color_scheme": "light",
    "extra_http_headers": {
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
}


def get_candidatos_rss(url):
    """Los primeros títulos del feed, en su orden (el feed de portada va por jerarquía)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        root = ET.fromstring(r.read())
    candidatos = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if title:
            candidatos.append({"text": title})
        if len(candidatos) == MAX_CANDIDATOS_RSS:
            break
    return candidatos


# Contenedores de banners de cookies / consentimiento: su texto no es un titular.
CONSENT_SELECTOR = ", ".join(
    f"[{attr}*='{word}' i]"
    for attr in ("id", "class")
    for word in ("cookie", "consent", "didomi", "onetrust", "cmp", "gdpr", "sp_message")
)

# Candidatos = enlaces visibles de la parte alta de la portada, de mayor a menor
# tamaño de letra (a igual tamaño, el que está más arriba). El primero es el que
# elegiría la heurística sola; el tamaño y la posición le sirven a la IA de pista.
CANDIDATES_JS = """
([minLen, maxY, consentSel, max]) => {
  const seen = new Map();
  for (const a of document.querySelectorAll('a')) {
    // <body> lleva clases como "didomi-popup-open" mientras hay banner: no cuenta
    const banner = a.closest(consentSel);
    if (banner && banner !== document.body && banner !== document.documentElement) continue;
    const r = a.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const y = Math.round(r.top + window.scrollY);
    if (y > maxY) continue;
    const text = a.innerText.trim().split('\\n')
      .map(s => s.trim()).sort((p, q) => q.length - p.length)[0] || '';
    if (text.length <= minLen) continue;
    let fs = parseFloat(getComputedStyle(a).fontSize);
    for (const el of a.querySelectorAll('*')) {
      if (el.innerText && el.innerText.trim() === text) {
        fs = Math.max(fs, parseFloat(getComputedStyle(el).fontSize));
      }
    }
    fs = Math.round(fs);
    const prev = seen.get(text);
    if (!prev || fs > prev.fs || (fs === prev.fs && y < prev.y)) seen.set(text, {text, fs, y});
  }
  return [...seen.values()]
    .sort((p, q) => q.fs - p.fs || p.y - q.y)
    .slice(0, max);
}
"""
HEADLINE_MAX_Y = 2500


def get_candidatos_web(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass  # hay portadas que nunca quedan en reposo; seguimos con lo cargado
    return page.evaluate(
        CANDIDATES_JS, [LOGO_MAX_LEN, HEADLINE_MAX_Y, CONSENT_SELECTOR, MAX_CANDIDATOS]
    )


def recoger_candidatos(medios):
    """{ nombre: [candidatos] } o { nombre: "[Error: …]" } si no se pudo leer la portada."""
    resultado = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(**CONTEXT_OPTS)
        page = context.new_page()
        Stealth().apply_stealth_sync(page)

        for medio in medios:
            nombre = medio["nombre"]
            try:
                if "rss" in medio:
                    candidatos = get_candidatos_rss(medio["rss"])
                else:
                    candidatos = get_candidatos_web(page, medio["url"])
                resultado[nombre] = candidatos or "[No encontrado]"
            except Exception as e:
                resultado[nombre] = f"[Error: {e}]"
            n = len(resultado[nombre]) if isinstance(resultado[nombre], list) else resultado[nombre]
            print(f"  {nombre:<18}  candidatos: {n}")

        browser.close()
    return resultado


def main():
    ahora = datetime.now(MADRID)
    with open(MEDIOS_FILE, encoding="utf-8") as f:
        medios = json.load(f)

    print(f"\n{'─' * 70}")
    print(f"  TITULARES  ·  {ahora.strftime('%d/%m/%Y %H:%M')}")
    print(f"{'─' * 70}\n")

    candidatos = recoger_candidatos(medios)
    validos = {n: c for n, c in candidatos.items() if isinstance(c, list)}
    clasificacion = clasificar(validos)  # {} si no hay IA: se usa la heurística

    resultados = []
    print()
    for medio in medios:
        nombre = medio["nombre"]
        item = {"media": nombre, "mediaUrl": medio["mediaUrl"]}
        if nombre not in validos:
            item.update(headline=candidatos[nombre], clasificado=False)
        elif nombre in clasificacion:
            c = clasificacion[nombre]
            item.update(
                headline=validos[nombre][c["candidato"]]["text"],
                tematica=c["tematica"],
                historia=c["historia"],
                clasificado=True,
            )
        else:
            item.update(headline=validos[nombre][0]["text"], clasificado=False)
        resultados.append(item)
        print(f"  {nombre:<18}  {'IA ' if item['clasificado'] else '   '} {item['headline']}")

    print(f"\n{'─' * 70}\n")

    contenido = json.dumps(resultados, ensure_ascii=False, indent=2)
    Path(OUTPUT_FILE).write_text(contenido, encoding="utf-8")
    copia = HISTORICO_DIR / ahora.strftime("%Y/%m") / f"{ahora.strftime('%Y-%m-%d_%H%M')}.json"
    copia.parent.mkdir(parents=True, exist_ok=True)
    copia.write_text(contenido, encoding="utf-8")
    print(f"  JSON guardado en {OUTPUT_FILE} y {copia}\n")


if __name__ == "__main__":
    main()
