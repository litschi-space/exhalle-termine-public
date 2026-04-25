#!/usr/bin/env python3
"""
Flask-Web-App für den ExHalle-Scraper.
Dient die generierte HTML-Seite aus und aktualisiert sie automatisch
alle 12 Stunden im Hintergrund.
"""

import os
import threading
import logging
from datetime import datetime
from pathlib import Path
from flask import Flask, Response

from scraper import get_months_to_scrape, fetch_calendar_page, parse_events, generate_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

MAINTENANCE_FILE = Path(__file__).parent / "maintenance"

_MAINTENANCE_HTML = """\
<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nicht verfügbar</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: #1a1a1a;
      font-family: 'Segoe UI', system-ui, sans-serif;
      color: #e0e0e0;
    }
    .card {
      text-align: center;
      padding: 3rem 2rem;
      max-width: 480px;
    }
    h1 {
      font-size: 1.4rem;
      font-weight: 600;
      margin-bottom: 0.75rem;
      color: #ffffff;
    }
    p { font-size: 1rem; color: #999; line-height: 1.6; }
    footer {
      position: fixed;
      bottom: 1.5rem;
      width: 100%;
      text-align: center;
      font-size: 0.85rem;
    }
    footer a {
      color: #666;
      text-decoration: none;
      margin: 0 0.75rem;
    }
    footer a:hover { color: #aaa; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Die Seite ist zurzeit nicht verfügbar.</h1>
    <p>Bitte schau später noch einmal vorbei.</p>
  </div>
  <footer>
    <a href="https://litschi.space" target="_blank" rel="noopener">litschi.space</a>
    <a href="https://litschi.space/Impressum.html" target="_blank" rel="noopener">Impressum</a>
  </footer>
</body>
</html>
"""

# Gecachtes HTML und Zeitstempel des letzten Scrape-Laufs
_cache: dict = {"html": None, "updated_at": None}
_lock = threading.Lock()


def _resolve_ticket_status(event: dict) -> str:
    """
    Leitet einen maschinenlesbaren Ticket-Status aus den Scraper-Daten ab.
    Rückgabewerte:
      "available"  – Karten verfügbar (Kauf-Link vorhanden)
      "soldOut"    – Derzeit ausverkauft
      "lastTickets"– Nur noch Restkarten / Stehplätze
      "notPublic"  – Kein Verkauf / kein Button (z.B. Schulvorstellung)
      ""           – Unbekannt / kein Status
    """
    tt = (event.get("ticket_text") or "").lower()
    tl = event.get("ticket_link") or ""

    if "ausverkauft" in tt:
        return "soldOut"
    if "restkarten" in tt or "stehplätze" in tt or "stehplaetze" in tt:
        return "available"
    if tl:
        # Kauf-Link vorhanden → verfügbar
        return "available"
    if tt:
        # Text aber kein Link (z.B. "Nur für Schulen") → nicht im freien Verkauf
        return "notPublic"
    return ""


def run_scraper():
    """Führt den Scraper aus und aktualisiert den Cache."""
    log.info("Scraper gestartet ...")
    all_events = []
    months = get_months_to_scrape()

    for monat in months:
        soup = fetch_calendar_page(monat)
        if soup:
            events = parse_events(soup, monat)
            all_events.extend(events)
            log.info(f"  {monat[:2]}/{monat[2:]}: {len(events)} Termine")
        else:
            log.warning(f"  {monat[:2]}/{monat[2:]}: Fehler beim Laden")

    # Duplikate entfernen
    seen = set()
    unique_events = []
    for e in all_events:
        key = (e.get("datum_iso"), e["titel"], e.get("uhrzeit") or "")
        if key not in seen:
            seen.add(key)
            unique_events.append(e)

    html = generate_html(unique_events)

    with _lock:
        _cache["html"] = html
        _cache["events"] = unique_events
        _cache["updated_at"] = datetime.now()

    log.info(f"Scraper fertig: {len(unique_events)} ExHalle-Termine im Cache.")


def schedule_loop(interval_hours: int = 12):
    """Läuft den Scraper einmal sofort, danach alle interval_hours Stunden."""
    run_scraper()

    timer = threading.Event()
    while not timer.wait(interval_hours * 3600):
        run_scraper()


@app.route("/")
def index():
    if MAINTENANCE_FILE.exists():
        return Response(_MAINTENANCE_HTML, status=503, mimetype="text/html; charset=utf-8")

    with _lock:
        html = _cache["html"]

    if html is None:
        return Response(
            "<html><body><p>Scraper läuft noch, bitte kurz warten und neu laden…</p></body></html>",
            status=503,
            mimetype="text/html; charset=utf-8",
        )
    return Response(html, mimetype="text/html; charset=utf-8")


@app.route("/api/heute")
def api_heute():
    """Gibt alle heutigen ExHalle-Vorstellungen als JSON zurück."""
    from datetime import date
    today_iso = date.today().isoformat()

    with _lock:
        events = _cache.get("events", [])

    heute = [
        {
            "titel": e["titel"],
            "uhrzeit": e["uhrzeit"],
            "datum_iso": e["datum_iso"],
            "ticket_text": e.get("ticket_text", ""),
            "ticket_link": e.get("ticket_link", ""),
            "ticket_status": _resolve_ticket_status(e),
        }
        for e in events
        if e.get("datum_iso") == today_iso
    ]
    return {"datum": today_iso, "vorstellungen": heute}


@app.route("/api/alle")
def api_alle():
    """Gibt alle zukünftigen ExHalle-Vorstellungen als JSON zurück."""
    from datetime import date
    today_iso = date.today().isoformat()

    with _lock:
        events = _cache.get("events", [])

    vorstellungen = [
        {
            "titel": e["titel"],
            "uhrzeit": e["uhrzeit"],
            "datum_iso": e["datum_iso"],
            "tag_monat": e.get("tag_monat", ""),
            "wochentag": e.get("wochentag", ""),
            "detail_link": e.get("detail_link", ""),
            "status": e.get("status", ""),
            "ticket_text": e.get("ticket_text", ""),
            "ticket_link": e.get("ticket_link", ""),
            "ticket_status": _resolve_ticket_status(e),
        }
        for e in events
        if e.get("datum_iso") and e["datum_iso"] >= today_iso
    ]
    return {"vorstellungen": vorstellungen}


@app.route("/health")
def health():
    with _lock:
        updated_at = _cache["updated_at"]
    status = "ok" if updated_at else "pending"
    ts = updated_at.isoformat() if updated_at else "—"
    return {"status": status, "last_update": ts}


# Scraper-Thread beim Import starten (funktioniert sowohl mit gunicorn als auch direkt)
_t = threading.Thread(target=schedule_loop, kwargs={"interval_hours": 12}, daemon=True)
_t.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
