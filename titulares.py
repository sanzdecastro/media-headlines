#!/usr/bin/env python3
"""
Extrae el titular principal de los 8 medios digitales españoles más visitados (OJD).
Genera titulares.json con la estructura { media, title, mediaUrl }.

La mayoría se obtiene via Playwright con emulación completa de navegador real.
El País bloquea headless a nivel de red (Akamai), por lo que se usa su RSS.
"""
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

OUTPUT_FILE = "titulares.json"

MEDIOS_FILE = "medios.json"
TIMEOUT = 20000
LOGO_MAX_LEN = 30

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


def get_titular_rss(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = r.read()
    root = ET.fromstring(data)
    item = root.find(".//item")
    if item is not None:
        title = item.findtext("title")
        if title and title.strip():
            return title.strip()
    return "[No encontrado]"


def get_titular_web(page, url):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)

        # 1. Primer <article> con h2/h3 — titular real de la noticia destacada
        el = page.query_selector("article h2, article h3")
        if el:
            text = el.inner_text().strip().split("\n")[0]
            if len(text) > LOGO_MAX_LEN:
                return text

        # 2. Si llegó vacía (anti-bot parcial), esperar carga completa
        body = page.query_selector("body")
        if body and len(body.inner_text().strip()) < 300:
            page.wait_for_load_state("networkidle", timeout=TIMEOUT)
            el = page.query_selector("article h2, article h3")
            if el:
                text = el.inner_text().strip().split("\n")[0]
                if len(text) > LOGO_MAX_LEN:
                    return text

        # 3. h1 con contenido real (no logo)
        el = page.query_selector("h1")
        if el:
            h1 = el.inner_text().strip().split("\n")[0]
            if len(h1) > LOGO_MAX_LEN:
                return h1

        # 4. Primer h2
        el = page.query_selector("h2")
        if el:
            h2 = el.inner_text().strip().split("\n")[0]
            if h2:
                return h2

    except Exception as e:
        return f"[Error: {e}]"

    return "[No encontrado]"


def main():
    with open(MEDIOS_FILE, encoding="utf-8") as f:
        medios = json.load(f)

    print(f"\n{'─' * 70}")
    print(f"  TITULARES  ·  {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'─' * 70}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(**CONTEXT_OPTS)
        page = context.new_page()
        Stealth().apply_stealth_sync(page)

        resultados = []
        for medio in medios:
            nombre = medio["nombre"]
            print(f"  {nombre:<18}", end="", flush=True)

            if "rss" in medio:
                try:
                    titular = get_titular_rss(medio["rss"])
                except Exception as e:
                    titular = f"[RSS error: {e}]"
            else:
                titular = get_titular_web(page, medio["url"])

            print(f"  {titular}")
            resultados.append({
                "media": nombre,
                "headline": titular,
                "mediaUrl": medio["mediaUrl"],
            })

        browser.close()

    print(f"\n{'─' * 70}\n")

    Path(OUTPUT_FILE).write_text(
        json.dumps(resultados, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  JSON guardado en {OUTPUT_FILE}\n")


if __name__ == "__main__":
    main()
