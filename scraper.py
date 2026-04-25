#!/usr/bin/env python3
"""
Staatstheater ExHalle Termine Scraper
Liest den Kalender von staatstheater.de und filtert alle ExHalle-Vorstellungen heraus.
Gibt eine HTML-Seite aus: exhalle_termine.html
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
import calendar
import json
import re
import time

BERLIN = ZoneInfo("Europe/Berlin")

MAX_RETRIES = 3

# Spielzeitpause – bei Saisonwechsel hier anpassen
SPIELZEITPAUSE_START = "2026-07-12"
SPIELZEITPAUSE_END   = "2026-08-25"

BASE_URL = "https://staatstheater.de"
KALENDER_URL = f"{BASE_URL}/kalender"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9",
}

# Monate die gescrapt werden (MMYYYY)
# Aktuell: aktuelle Spielzeit bis Ende
def get_months_to_scrape():
    """Gibt die zu scrapenden Monate zurück, beginnend mit dem aktuellen Monat."""
    today = date.today()
    months = []
    year = today.year
    month = today.month
    # Scrape 12 Monate ab heute
    for _ in range(12):
        months.append(f"{month:02d}{year}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def fetch_calendar_page(monat: str) -> BeautifulSoup | None:
    """Lädt eine Kalenderseite und gibt ein BeautifulSoup-Objekt zurück."""
    url = f"{KALENDER_URL}?monat={monat}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            # 4xx/5xx = Server hat keine Daten für diesen Monat → nicht wiederholen
            if response.status_code >= 400:
                return None
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except requests.RequestException as e:
            # Nur echte Netzwerkfehler (Timeout, Connection) werden wiederholt
            print(f"Versuch {attempt}/{MAX_RETRIES} fehlgeschlagen für {url}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
    return None


def parse_events(soup: BeautifulSoup, monat: str) -> list[dict]:
    """Parst alle ExHalle-Events aus einer Kalenderseite."""
    events = []

    # Kalender-Container (Desktop-Version hat die strukturierten Daten)
    kalender = soup.find("div", class_=re.compile(r"\bkalender\b"))
    if not kalender:
        kalender = soup.find("main")
    if not kalender:
        return events

    # Alle Tages-Container
    for tag_div in kalender.find_all("div", class_="tag"):
        # Datum aus dem Tag-Container lesen
        datum_div = tag_div.find("div", class_="datum")
        if not datum_div:
            continue

        wochentag_span = datum_div.find("span", class_="wochentag")
        tag_monat_span = datum_div.find("span", class_="tag-monat")
        date_anchor = datum_div.find("a", class_="date-dmY")

        wochentag = wochentag_span.get_text(strip=True) if wochentag_span else ""
        tag_monat = tag_monat_span.get_text(strip=True) if tag_monat_span else ""

        # Datum parsen aus dem anchor name (Format: DDMMYYYY)
        datum_iso = None
        if date_anchor:
            anchor_name = date_anchor.get("name", "")
            if len(anchor_name) == 8:
                try:
                    datum_iso = datetime.strptime(anchor_name, "%d%m%Y").strftime("%Y-%m-%d")
                except ValueError:
                    pass

        # Alle Aufführungen in diesem Tag prüfen
        for auffuehrung in tag_div.find_all("div", class_="auffuehrung"):
            # Spielort prüfen
            spielort_span = auffuehrung.find("span", class_="spielort")
            if not spielort_span:
                continue
            spielort = spielort_span.get_text(strip=True)
            if "exhalle" not in spielort.lower():
                continue

            # Status (Premiere, Wiederaufnahme, etc.)
            status_span = auffuehrung.find("span", class_="status")
            status = status_span.get_text(strip=True) if status_span else ""

            # Zusätzliche CSS-Klassen des auffuehrung-divs (z.B. 'premiere', 'wiederaufnahme')
            css_classes = auffuehrung.get("class", [])
            extra_classes = [c for c in css_classes if c != "auffuehrung"]

            # Titel und Detail-Link
            titel_tag = auffuehrung.find("h2")
            titel = ""
            detail_link = ""
            if titel_tag:
                titel = titel_tag.get_text(strip=True)
                a_tag = titel_tag.find("a")
                if a_tag and a_tag.get("href"):
                    href = a_tag["href"]
                    detail_link = href if href.startswith("http") else BASE_URL + href

            # Uhrzeit
            uhrzeit_p = auffuehrung.find("p", class_="uhrzeit")
            uhrzeit = uhrzeit_p.get_text(strip=True) if uhrzeit_p else ""

            # Beschreibung (alle p-Tags außer spielort und uhrzeit)
            beschreibung_parts = []
            for p in auffuehrung.find_all("p"):
                p_classes = p.get("class", [])
                if "spielort" not in p_classes and "uhrzeit" not in p_classes:
                    text = p.get_text(strip=True)
                    if text:
                        beschreibung_parts.append(text)
            beschreibung = " | ".join(beschreibung_parts)

            # Ticket-Link und Status
            ticket_link_tag = auffuehrung.find("a", class_="link-ticket")
            ticket_link = ""
            ticket_text = ""
            if ticket_link_tag:
                ticket_link = ticket_link_tag.get("href", "")
                ticket_text = ticket_link_tag.get_text(strip=True)
            else:
                # Ausverkauft, Restkarten oder kein Ticket-Button
                full_text = auffuehrung.get_text(" ", strip=True)
                if "ausverkauft" in full_text.lower():
                    ticket_text = "Derzeit ausverkauft"
                elif "restkarten" in full_text.lower():
                    ticket_text = "Restkarten"
                    # Link trotzdem aus einem beliebigen a-Tag holen
                    fallback_link = auffuehrung.find("a", href=re.compile(r"ticket|eventim|shop", re.I))
                    if fallback_link:
                        ticket_link = fallback_link.get("href", "")
                elif "stehplätze" in full_text.lower():
                    ticket_text = "Nur noch Stehplätze"
                    fallback_link = auffuehrung.find("a", href=re.compile(r"ticket|eventim|shop", re.I))
                    if fallback_link:
                        ticket_link = fallback_link.get("href", "")

            events.append({
                "datum_iso": datum_iso,
                "wochentag": wochentag,
                "tag_monat": tag_monat,
                "status": status,
                "titel": titel,
                "uhrzeit": uhrzeit,
                "beschreibung": beschreibung,
                "detail_link": detail_link,
                "ticket_link": ticket_link,
                "ticket_text": ticket_text,
                "monat": monat,
            })

    return events


def generate_timeline(events: list[dict], today_iso: str,
                      pause_start_iso: str, pause_end_iso: str) -> str:
    """Generiert den horizontalen Zeitstrahl als HTML-String."""
    dates_map: dict[str, list] = defaultdict(list)
    for e in events:
        if e.get("datum_iso"):
            dates_map[e["datum_iso"]].append(e)

    if not dates_map:
        return ""

    today       = date.fromisoformat(today_iso)
    pause_start = date.fromisoformat(pause_start_iso)
    pause_end   = date.fromisoformat(pause_end_iso)

    all_dates   = sorted(dates_map)
    last_event  = date.fromisoformat(all_dates[-1])
    # Zeitstrahl endet beim letzten Event oder einen Monat nach Pause-Ende
    timeline_end = max(last_event, pause_end + timedelta(days=30))

    MON = ["Jan","Feb","Mär","Apr","Mai","Jun","Jul","Aug","Sep","Okt","Nov","Dez"]

    # Monatsliste ab heute bis timeline_end
    months: list[tuple[int, int]] = []
    y, m = today.year, today.month
    while (y, m) <= (timeline_end.year, timeline_end.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1; y += 1

    def fmt_date(d: date) -> str:
        return f"{d.day}.&nbsp;{MON[d.month - 1]}"

    segs: list[str] = []
    pause_inserted = False

    for y, m in months:
        days_in_month = calendar.monthrange(y, m)[1]
        month_start   = date(y, m, 1)
        month_end     = date(y, m, days_in_month)

        # Monat überspringen wenn er die Pause überlappt und keine Events hat
        overlaps_pause = (month_start <= pause_end and month_end >= pause_start)
        prefix = f"{y}-{m:02d}-"
        month_events = {d: dates_map[d] for d in dates_map if d.startswith(prefix)}
        if overlaps_pause and not month_events:
            continue

        # Pause-Segment einmalig vor dem ersten Monat nach der Pause
        if not pause_inserted and month_start > pause_end:
            segs.append(
                '<div class="tl-seg tl-pause-seg">'
                '<div class="tl-seg-label">&#9728;&#65039;&nbsp;Spielzeitpause</div>'
                f'<div class="tl-pause-dates">{fmt_date(pause_start)}&nbsp;&ndash;&nbsp;{fmt_date(pause_end)}</div>'
                '</div>'
            )
            pause_inserted = True

        # Event-Dots
        dots_parts: list[str] = []
        for iso_d in sorted(month_events):
            d       = date.fromisoformat(iso_d)
            pct     = (d.day - 1) / max(days_in_month - 1, 1) * 100
            day_evts = month_events[iso_d]
            is_prem  = any(e.get("status", "").lower() == "premiere" for e in day_evts)
            is_today = iso_d == today_iso
            cls      = "tl-dot" + (" tl-dot-premiere" if is_prem else "") + (" tl-dot-today" if is_today else "")
            wt       = day_evts[0].get("wochentag", "")
            tm_str   = day_evts[0].get("tag_monat", "")
            n        = len(day_evts)
            tip_sfx  = f" · {n}\u00a0Vorstellungen" if n > 1 else f" · {day_evts[0]['titel'][:28]}"
            tip      = f"{wt}, {tm_str}{tip_sfx}"
            dots_parts.append(
                f'<a class="{cls}" href="#evt-{iso_d}" style="left:{pct:.1f}%" title="{tip}"></a>'
            )

        # Heute-Marker (senkrechte Linie)
        today_marker = ""
        if today.year == y and today.month == m:
            tpct         = (today.day - 1) / max(days_in_month - 1, 1) * 100
            today_marker = f'<div class="tl-now" id="tlNow" style="left:{tpct:.1f}%"></div>'

        show_year = (m == 1 or (y, m) == months[0])
        yr_span   = f'<span class="tl-yr"> {y}</span>' if show_year else ""

        segs.append(
            '<div class="tl-seg tl-month-seg">'
            f'<div class="tl-seg-label">{MON[m - 1]}{yr_span}</div>'
            '<div class="tl-rail-wrap">'
            '<div class="tl-rail"></div>'
            f'{today_marker}'
            f'{"".join(dots_parts)}'
            '</div>'
            '</div>'
        )

    return (
        '<div class="timeline-bar" role="navigation" aria-label="Terminübersicht">'
        '<div class="tl-inner" id="tlInner">'
        + "".join(segs) +
        '</div>'
        '</div>'
    )


def generate_html(events: list[dict]) -> str:
    """Generiert eine HTML-Seite mit allen ExHalle-Terminen."""

    # Events nach Datum sortieren
    def sort_key(e):
        return e.get("datum_iso") or "9999"

    events.sort(key=sort_key)

    # Heute für "vergangene Termine" Markierung
    now = datetime.now(BERLIN)
    today_iso = now.date().isoformat()

    def ticket_badge(event):
        tt = event["ticket_text"]
        tl = event["ticket_link"]
        if not tt and not tl:
            return ""
        badge_class = "ticket-available"
        if "ausverkauft" in tt.lower():
            badge_class = "ticket-sold-out"
        elif "restkarten" in tt.lower():
            badge_class = "ticket-last"
        elif "stehplätze" in tt.lower():
            badge_class = "ticket-standing"

        if tl:
            return f'<a href="{tl}" class="ticket-badge {badge_class}" target="_blank" rel="noopener">{tt}</a>'
        return f'<span class="ticket-badge {badge_class}">{tt}</span>'

    def status_badge(event):
        s = event["status"]
        if not s:
            return ""
        return f'<span class="status-badge">{s}</span>'

    seen_anchor_dates: set[str] = set()

    def event_card(event):
        datum = event.get("datum_iso", "9999")
        anchor_attr = ""
        if datum != "9999" and datum not in seen_anchor_dates:
            seen_anchor_dates.add(datum)
            anchor_attr = f' id="evt-{datum}"'
        is_today = datum == today_iso

        # Vergangen: Datum vor heute, oder heute aber Uhrzeit bereits vorbei
        if datum < today_iso:
            past = "past"
        elif is_today:
            # Endzeit aus "HH:MM - HH:MM Uhr" parsen, fallback auf Startzeit
            times = re.findall(r"(\d{1,2}):(\d{2})", event.get("uhrzeit", ""))
            ref_h, ref_m = (int(times[-1][0]), int(times[-1][1])) if times else (None, None)
            if ref_h is not None:
                past = "past" if now.hour > ref_h or (now.hour == ref_h and now.minute >= ref_m) else ""
            else:
                past = ""
        else:
            past = ""

        heute_badge = '<span class="heute-badge">Heute</span>' if is_today else ""

        detail = event["detail_link"]
        titel_html = f'<a href="{detail}" target="_blank" rel="noopener">{event["titel"]}</a>' if detail else event["titel"]

        beschreibung_html = ""
        if event["beschreibung"]:
            parts = event["beschreibung"].split(" | ")
            beschreibung_html = "".join(f'<p class="beschreibung">{p}</p>' for p in parts if p)

        is_premiere = event.get("status", "").lower() == "premiere"

        return f"""
        <div class="event-card {past}{'premiere' if is_premiere else ''}"{anchor_attr}>
            <div class="event-date">
                <span class="wochentag">{event["wochentag"]}</span>
                <span class="tag-monat">{event["tag_monat"]}</span>
                {heute_badge}
            </div>
            <div class="event-info">
                <div class="event-header">
                    <h3 class="event-titel">{titel_html}</h3>
                    {status_badge(event)}
                </div>
                <p class="event-uhrzeit">{event["uhrzeit"]}</p>
                {beschreibung_html}
                <div class="event-footer">
                    {ticket_badge(event)}
                    {"" if not detail else f'<a href="{detail}" class="mehr-link" target="_blank" rel="noopener">Mehr Infos →</a>'}
                </div>
            </div>
        </div>"""

    cards_html = "\n".join(event_card(e) for e in events)

    now_str      = now.strftime("%d.%m.%Y %H:%M")
    upcoming     = sum(1 for e in events if e.get("datum_iso", "9999") >= today_iso)
    timeline_html = generate_timeline(events, today_iso, SPIELZEITPAUSE_START, SPIELZEITPAUSE_END)

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ExHalle – Termine | Oldenburgisches Staatstheater</title>
    <style>
        :root {{
            --exhalle-red: #c0392b;
            --exhalle-dark: #1a1a1a;
            --exhalle-grey: #f5f5f5;
            --exhalle-mid: #888;
            --card-border: #e0e0e0;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: 'Segoe UI', Arial, sans-serif;
            background: var(--exhalle-grey);
            color: var(--exhalle-dark);
            line-height: 1.5;
        }}

        header {{
            background: var(--exhalle-dark);
            color: white;
            padding: 2rem 1.5rem;
            text-align: center;
        }}

        header h1 {{
            font-size: 2rem;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            margin-bottom: 0.4rem;
        }}

        header .subtitle {{
            color: var(--exhalle-red);
            font-size: 1rem;
            text-transform: uppercase;
            letter-spacing: 0.15em;
        }}

        .meta {{
            text-align: center;
            padding: 1rem;
            font-size: 0.85rem;
            color: var(--exhalle-mid);
            background: white;
            border-bottom: 1px solid var(--card-border);
        }}

        .meta strong {{ color: var(--exhalle-dark); }}

        main {{
            max-width: 900px;
            margin: 2rem auto;
            padding: 0 1rem;
        }}

        .event-card {{
            background: white;
            border-radius: 6px;
            border: 1px solid var(--card-border);
            display: flex;
            gap: 0;
            margin-bottom: 1rem;
            transition: box-shadow 0.15s;
            overflow: hidden;
            position: relative;
        }}

        .event-card:hover {{
            box-shadow: 0 4px 20px rgba(0,0,0,0.12);
        }}

        .event-card.past {{
            opacity: 0.45;
        }}

        /* Im Stack: nur die Karte selbst ausgrauen, nicht über opacity
           damit die Schatten-Layer dahinter normal bleiben */
        .card-stack-top .event-card.past {{
            opacity: 1;
            filter: grayscale(40%) brightness(1.1);
            color: #aaa;
        }}

        .card-stack-top .event-card.past .event-date {{
            background: #555;
        }}

        .card-stack-top .event-card.past .event-date .wochentag {{
            color: #999;
        }}

        .card-stack-top .event-card.past .event-titel a,
        .card-stack-top .event-card.past .event-titel {{
            color: #aaa;
        }}

        .card-stack-top .event-card.past .event-uhrzeit,
        .card-stack-top .event-card.past .beschreibung,
        .card-stack-top .event-card.past .mehr-link {{
            color: #bbb;
        }}

        /* Ticket-Abriss: gezackte Trennlinie */
        .event-card::before {{
            content: '';
            position: absolute;
            left: 100px;
            top: -1px;
            bottom: -1px;
            width: 0;
            border-left: 2px dashed var(--card-border);
            z-index: 1;
            pointer-events: none;
        }}

        /* Ticket-Stub (linke Seite: Datum) */
        .event-date {{
            flex-shrink: 0;
            width: 100px;
            background: var(--exhalle-dark);
            color: white;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 1.2rem 0.8rem;
            gap: 0.1rem;
            text-align: center;
            position: relative;
        }}

        /* Halbkreis-Ausstanzung, zentriert auf die Karte */
        .event-card .event-date::after {{
            content: '';
            position: absolute;
            right: -10px;
            top: 50%;
            transform: translateY(-50%);
            width: 20px;
            height: 20px;
            background: var(--exhalle-grey);
            border-radius: 50%;
            border: 1px solid var(--card-border);
            z-index: 2;
        }}

        .event-date .wochentag {{
            display: block;
            font-size: 0.7rem;
            text-transform: uppercase;
            color: var(--exhalle-red);
            letter-spacing: 0.1em;
            font-weight: 700;
        }}

        .event-date .tag-monat {{
            display: block;
            font-size: 1.05rem;
            font-weight: 700;
            margin-top: 0.15rem;
            letter-spacing: 0.02em;
        }}

        /* Ticket-Hauptbereich */
        .event-info {{
            flex: 1;
            padding: 1rem 1.2rem 1rem 1.4rem;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }}

        .event-header {{
            display: flex;
            align-items: flex-start;
            gap: 0.6rem;
            flex-wrap: wrap;
            margin-bottom: 0.25rem;
        }}

        .event-titel {{
            font-size: 1.05rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}

        .event-titel a {{
            color: inherit;
            text-decoration: none;
        }}

        .event-titel a:hover {{
            color: var(--exhalle-red);
        }}

        .event-uhrzeit {{
            font-size: 0.88rem;
            color: var(--exhalle-mid);
            margin-bottom: 0.3rem;
            letter-spacing: 0.02em;
        }}

        .beschreibung {{
            font-size: 0.85rem;
            color: #666;
            margin-bottom: 0.15rem;
        }}

        .event-footer {{
            display: flex;
            align-items: center;
            gap: 0.8rem;
            margin-top: 0.6rem;
            flex-wrap: wrap;
        }}

        .ticket-badge {{
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 3px;
            font-size: 0.75rem;
            font-weight: 700;
            text-decoration: none;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}

        .ticket-available {{
            background: var(--exhalle-red);
            color: white;
        }}

        .ticket-available:hover {{
            background: #a93226;
        }}

        .ticket-last {{
            background: #e67e22;
            color: white;
        }}

        .ticket-sold-out {{
            background: #ddd;
            color: #888;
        }}

        .ticket-standing {{
            background: #8e44ad;
            color: white;
        }}

        .status-badge {{
            display: inline-block;
            padding: 0.1rem 0.45rem;
            background: var(--exhalle-dark);
            color: white;
            font-size: 0.65rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            border-radius: 2px;
            flex-shrink: 0;
            align-self: center;
        }}

        .heute-badge {{
            display: block;
            margin-top: 0.4rem;
            padding: 0.15rem 0.4rem;
            background: var(--exhalle-red);
            color: white;
            font-size: 0.6rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            border-radius: 2px;
            text-align: center;
        }}

        .mehr-link {{
            font-size: 0.82rem;
            color: var(--exhalle-mid);
            text-decoration: none;
            letter-spacing: 0.02em;
        }}

        .mehr-link:hover {{
            color: var(--exhalle-red);
            text-decoration: underline;
        }}

        .no-events {{
            text-align: center;
            padding: 3rem;
            color: var(--exhalle-mid);
        }}

        footer {{
            text-align: center;
            padding: 2rem 1rem;
            font-size: 0.8rem;
            color: var(--exhalle-mid);
            border-top: 1px solid var(--card-border);
            margin-top: 2rem;
        }}

        .bug-report-link {{
            color: var(--exhalle-mid);
            text-decoration: none;
        }}

        .bug-report-link:hover {{
            color: var(--exhalle-red);
        }}

        /* === Premiere golden gradient === */
        .event-card.premiere {{
            background: linear-gradient(135deg, #fffbf0 0%, #fff8e1 40%, #fdf3d0 100%);
            border-color: #d4a843;
        }}

        .event-card.premiere .event-date {{
            background: linear-gradient(160deg, #2a1f00 0%, #1a1400 100%);
        }}

        .event-card.premiere .event-date .wochentag {{
            color: #d4a843;
        }}

        .event-card.premiere::before {{
            border-left-color: #d4a843;
        }}

        .event-card.premiere .event-date::after {{
            border-color: #d4a843;
        }}

        .card-stack-top.premiere-stack::before,
        .card-stack-top.premiere-stack::after {{
            background: linear-gradient(to right, #1a1400 100px, #fffbf0 100px);
            border-color: #d4a843;
        }}

        /* === Theme Toggle === */
        .theme-toggle {{
            position: absolute;
            top: 1rem;
            right: 1.2rem;
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 20px;
            color: white;
            font-size: 1rem;
            width: 2.2rem;
            height: 2.2rem;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.2s;
        }}

        .theme-toggle:hover {{
            background: rgba(255,255,255,0.2);
        }}

        header {{ position: relative; }}

        /* === Dark Mode === */
        @media (prefers-color-scheme: dark) {{
            html:not([data-theme="light"]) body {{ background: #111; color: #e8e8e8; }}
            html:not([data-theme="light"]) .meta {{ background: #1a1a1a; color: #777; border-bottom-color: #2a2a2a; }}
            html:not([data-theme="light"]) .meta strong {{ color: #e8e8e8; }}
            html:not([data-theme="light"]) .event-card {{ background: #1e1e1e; border-color: #2a2a2a; }}
            html:not([data-theme="light"]) .event-card::before {{ border-left-color: #2a2a2a; }}
            html:not([data-theme="light"]) .event-card:hover {{ box-shadow: 0 4px 20px rgba(0,0,0,0.5); }}
            html:not([data-theme="light"]) .event-date::after {{ background: #111; border-color: #2a2a2a; }}
            html:not([data-theme="light"]) .event-date {{ background: #0d0d0d; }}
            html:not([data-theme="light"]) .card-stack {{ background: #111; }}
            html:not([data-theme="light"]) .stack-toggle {{ color: #555; }}
            html:not([data-theme="light"]) .stack-toggle:hover {{ color: #e8e8e8; }}
            html:not([data-theme="light"]) .card-stack-top::before,
            html:not([data-theme="light"]) .card-stack-top::after {{ background: linear-gradient(to right, #0d0d0d 100px, #1e1e1e 100px); border-color: #2a2a2a; }}
            html:not([data-theme="light"]) .stack-toggle {{ color: #555; }}
            html:not([data-theme="light"]) .stack-toggle:hover {{ color: #e8e8e8; }}
            html:not([data-theme="light"]) .beschreibung {{ color: #999; }}
            html:not([data-theme="light"]) .event-uhrzeit {{ color: #666; }}
            html:not([data-theme="light"]) .ticket-sold-out {{ background: #2a2a2a; color: #666; }}
            html:not([data-theme="light"]) .status-badge {{ background: #2a2a2a; }}
            html:not([data-theme="light"]) footer {{ border-color: #2a2a2a; }}
            html:not([data-theme="light"]) footer a {{ color: var(--exhalle-red); }}

            html:not([data-theme="light"]) .event-card.premiere {{ background: linear-gradient(135deg, #1e1800 0%, #181200 40%, #120e00 100%); border-color: #7a5f1a; }}
            html:not([data-theme="light"]) .event-card.premiere .event-date {{ background: linear-gradient(160deg, #0d0a00 0%, #080600 100%); }}
            html:not([data-theme="light"]) .event-card.premiere::before {{ border-left-color: #7a5f1a; }}
            html:not([data-theme="light"]) .event-card.premiere .event-date::after {{ border-color: #7a5f1a; background: #111; }}
            html:not([data-theme="light"]) .card-stack-top.premiere-stack::before,
            html:not([data-theme="light"]) .card-stack-top.premiere-stack::after {{ background: linear-gradient(to right, #080600 100px, #1e1800 100px); border-color: #7a5f1a; }}
            html:not([data-theme="light"]) .card-stack-rest .event-card {{ background: #1e1e1e; border-color: #2a2a2a; }}
            html:not([data-theme="light"]) .card-stack-rest .event-card .event-date {{ background: #0d0d0d; }}
            html:not([data-theme="light"]) .card-stack-rest .event-card::before {{ border-left-color: #2a2a2a; }}
            html:not([data-theme="light"]) .timeline-bar {{ background: var(--exhalle-dark); border-bottom-color: #222; scrollbar-color: #333 var(--exhalle-dark); }}
            html:not([data-theme="light"]) .timeline-bar::-webkit-scrollbar-track {{ background: var(--exhalle-dark); }}
            html:not([data-theme="light"]) .timeline-bar::-webkit-scrollbar-thumb {{ background: #333; }}
            html:not([data-theme="light"]) .tl-month-seg {{ border-right-color: #252525; }}
            html:not([data-theme="light"]) .tl-seg-label {{ color: #555; }}
            html:not([data-theme="light"]) .tl-yr {{ color: #3a3a3a; }}
            html:not([data-theme="light"]) .tl-rail {{ background: #2a2a2a; }}
            html:not([data-theme="light"]) .tl-dot:not(.tl-dot-premiere):not(.tl-dot-today) {{ background: #555; }}
            html:not([data-theme="light"]) .tl-dot:hover {{ background: #ccc; }}
            html:not([data-theme="light"]) .tl-pause-seg {{ background: repeating-linear-gradient(-45deg, transparent, transparent 5px, rgba(255,255,255,0.022) 5px, rgba(255,255,255,0.022) 10px); border-color: #252525; }}
            html:not([data-theme="light"]) .tl-pause-dates {{ color: #3a3a3a; }}
        }}

        /* Dark Mode per Button erzwungen */
        html[data-theme="dark"] body {{ background: #111; color: #e8e8e8; }}
        html[data-theme="dark"] .meta {{ background: #1a1a1a; color: #777; border-bottom-color: #2a2a2a; }}
        html[data-theme="dark"] .meta strong {{ color: #e8e8e8; }}
        html[data-theme="dark"] .event-card {{ background: #1e1e1e; border-color: #2a2a2a; }}
        html[data-theme="dark"] .event-card::before {{ border-left-color: #2a2a2a; }}
        html[data-theme="dark"] .event-card:hover {{ box-shadow: 0 4px 20px rgba(0,0,0,0.5); }}
        html[data-theme="dark"] .event-date::after {{ background: #111; border-color: #2a2a2a; }}
        html[data-theme="dark"] .event-date {{ background: #0d0d0d; }}
        html[data-theme="dark"] .card-stack {{ background: #111; }}
        html[data-theme="dark"] .stack-toggle {{ color: #555; }}
        html[data-theme="dark"] .stack-toggle:hover {{ color: #e8e8e8; }}
        html[data-theme="dark"] .card-stack-top::before,
        html[data-theme="dark"] .card-stack-top::after {{ background: linear-gradient(to right, #0d0d0d 100px, #1e1e1e 100px); border-color: #2a2a2a; }}
        html[data-theme="dark"] .stack-toggle {{ color: #555; }}
        html[data-theme="dark"] .stack-toggle:hover {{ color: #e8e8e8; }}
        html[data-theme="dark"] .beschreibung {{ color: #999; }}
        html[data-theme="dark"] .event-uhrzeit {{ color: #666; }}
        html[data-theme="dark"] .ticket-sold-out {{ background: #2a2a2a; color: #666; }}
        html[data-theme="dark"] .status-badge {{ background: #2a2a2a; }}
        html[data-theme="dark"] footer {{ border-color: #2a2a2a; }}
        html[data-theme="dark"] footer a {{ color: var(--exhalle-red); }}

        html[data-theme="dark"] .event-card.premiere {{ background: linear-gradient(135deg, #1e1800 0%, #181200 40%, #120e00 100%); border-color: #7a5f1a; }}
        html[data-theme="dark"] .event-card.premiere .event-date {{ background: linear-gradient(160deg, #0d0a00 0%, #080600 100%); }}
        html[data-theme="dark"] .event-card.premiere::before {{ border-left-color: #7a5f1a; }}
        html[data-theme="dark"] .event-card.premiere .event-date::after {{ border-color: #7a5f1a; background: #111; }}
        html[data-theme="dark"] .card-stack-rest .event-card {{ background: #1e1e1e; border-color: #2a2a2a; }}
        html[data-theme="dark"] .card-stack-rest .event-card .event-date {{ background: #0d0d0d; }}
        html[data-theme="dark"] .card-stack-rest .event-card::before {{ border-left-color: #2a2a2a; }}
        html[data-theme="dark"] .timeline-bar {{ background: var(--exhalle-dark); border-bottom-color: #222; scrollbar-color: #333 var(--exhalle-dark); }}
        html[data-theme="dark"] .timeline-bar::-webkit-scrollbar-track {{ background: var(--exhalle-dark); }}
        html[data-theme="dark"] .timeline-bar::-webkit-scrollbar-thumb {{ background: #333; }}
        html[data-theme="dark"] .tl-month-seg {{ border-right-color: #252525; }}
        html[data-theme="dark"] .tl-seg-label {{ color: #555; }}
        html[data-theme="dark"] .tl-yr {{ color: #3a3a3a; }}
        html[data-theme="dark"] .tl-rail {{ background: #2a2a2a; }}
        html[data-theme="dark"] .tl-dot:not(.tl-dot-premiere):not(.tl-dot-today) {{ background: #555; }}
        html[data-theme="dark"] .tl-dot:hover {{ background: #ccc; }}
        html[data-theme="dark"] .tl-pause-seg {{ background: repeating-linear-gradient(-45deg, transparent, transparent 5px, rgba(255,255,255,0.022) 5px, rgba(255,255,255,0.022) 10px); border-color: #252525; }}
        html[data-theme="dark"] .tl-pause-dates {{ color: #3a3a3a; }}

        /* === Mobile: Ticket Layout beibehalten === */
        @media (max-width: 600px) {{
            .event-card {{ font-size: 0.9rem; }}
            .event-titel {{ font-size: 0.95rem; }}
            .event-info {{ padding: 0.9rem 0.9rem 0.9rem 1.2rem; }}
        }}

        /* === Zeitstrahl === */
        .timeline-bar {{
            background: white;
            border-bottom: 2px solid var(--card-border);
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: thin;
            scrollbar-color: #ccc white;
        }}

        .timeline-bar::-webkit-scrollbar {{ height: 3px; }}
        .timeline-bar::-webkit-scrollbar-track {{ background: white; }}
        .timeline-bar::-webkit-scrollbar-thumb {{ background: #ccc; border-radius: 2px; }}

        .tl-inner {{
            display: flex;
            align-items: stretch;
            padding: 0.75rem 1.5rem 0.65rem;
            width: max-content;
        }}

        .tl-seg {{
            display: flex;
            flex-direction: column;
            padding: 0 0.9rem;
            position: relative;
        }}

        .tl-month-seg {{
            min-width: 140px;
            border-right: 1px solid var(--card-border);
        }}

        .tl-month-seg:last-child {{ border-right: none; }}

        .tl-seg-label {{
            font-size: 0.62rem;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            color: #aaa;
            margin-bottom: 0.45rem;
            font-weight: 700;
            white-space: nowrap;
        }}

        .tl-yr {{
            color: #ccc;
            font-weight: 400;
            letter-spacing: 0.05em;
        }}

        .tl-rail-wrap {{
            position: relative;
            height: 20px;
        }}

        .tl-rail {{
            position: absolute;
            top: 50%; left: 0; right: 0;
            height: 1px;
            background: var(--card-border);
            transform: translateY(-50%);
        }}

        .tl-dot {{
            position: absolute;
            top: 50%;
            transform: translate(-50%, -50%);
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: #bbb;
            transition: transform 0.12s, background 0.12s;
            z-index: 2;
            text-decoration: none;
            cursor: pointer;
        }}

        .tl-dot:hover {{
            transform: translate(-50%, -50%) scale(2.2);
            background: #555;
        }}

        .tl-dot-premiere {{
            background: #d4a843;
            width: 9px;
            height: 9px;
        }}

        .tl-dot-today {{
            background: var(--exhalle-red);
            width: 9px;
            height: 9px;
            box-shadow: 0 0 6px rgba(192,57,43,0.7);
        }}

        .tl-now {{
            position: absolute;
            top: -4px; bottom: -4px;
            width: 2px;
            background: var(--exhalle-red);
            transform: translateX(-50%);
            border-radius: 1px;
            opacity: 0.45;
            z-index: 1;
        }}

        .tl-pause-seg {{
            min-width: 90px;
            padding: 0 1rem;
            justify-content: center;
            gap: 0.2rem;
            background: repeating-linear-gradient(
                -45deg,
                transparent,
                transparent 5px,
                rgba(0,0,0,0.04) 5px,
                rgba(0,0,0,0.04) 10px
            );
            border-left: 1px solid var(--card-border);
            border-right: 1px solid var(--card-border);
        }}

        .tl-pause-seg .tl-seg-label {{
            color: var(--exhalle-red);
            margin-bottom: 0.2rem;
        }}

        .tl-pause-dates {{
            font-size: 0.58rem;
            color: #bbb;
            white-space: nowrap;
            letter-spacing: 0.03em;
        }}

        /* === Card Stack === */
        .card-stack {{
            margin-bottom: 1rem;
        }}

        .card-stack-top {{
            position: relative;
        }}

        .card-stack-top::before,
        .card-stack-top::after {{
            content: '';
            position: absolute;
            left: 0; right: 0;
            background: linear-gradient(to right, var(--exhalle-dark) 100px, white 100px);
            border: 1px solid var(--card-border);
            border-radius: 6px;
            pointer-events: none;
        }}

        .card-stack-top::before {{
            top: 6px; bottom: -33px;
            transform: scaleX(0.97);
            z-index: 1;
        }}

        .card-stack-top::after {{
            top: 12px; bottom: -38px;
            transform: scaleX(0.94);
            z-index: 0;
        }}

        .card-stack[data-stack-size="2"] .card-stack-top::after {{ display: none; }}

        /* Schatten-Layer immer voll sichtbar */

        .card-stack-top .event-card {{
            position: relative;
            z-index: 2;
            margin-bottom: 0;
        }}

        .stack-toggle {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            width: 100%;
            margin-top: 0;
            padding: 0.45rem 1rem;
            background: transparent;
            border: none;
            color: var(--exhalle-mid);
            font-size: 0.78rem;
            font-family: inherit;
            letter-spacing: 0.04em;
            cursor: pointer;
            transition: color 0.15s;
            position: relative;
            z-index: 3;
        }}

        .stack-toggle:hover {{
            color: var(--exhalle-dark);
        }}

        .stack-toggle .toggle-arrow {{
            display: inline-block;
            transition: transform 0.25s ease;
            font-style: normal;
        }}

        .card-stack.is-open .stack-toggle .toggle-arrow {{
            transform: rotate(180deg);
        }}

        .card-stack.is-open .card-stack-top::before,
        .card-stack.is-open .card-stack-top::after {{
            display: none;
        }}

        .card-stack-rest {{
            overflow: hidden;
            max-height: 0;
            transition: max-height 0.35s ease;
        }}

        .card-stack-rest .event-card {{
            margin-top: 0.5rem;
            margin-bottom: 0;
            background: white;
            border-color: var(--card-border);
        }}

        .card-stack-rest .event-card .event-date {{
            background: var(--exhalle-dark);
        }}

        .card-stack-rest .event-card .event-date .wochentag {{
            color: var(--exhalle-red);
        }}

        .card-stack-rest .event-card::before {{
            border-left-color: var(--card-border);
        }}

        .card-stack.is-open .card-stack-rest {{
            max-height: 5000px;
        }}
    </style>
</head>
<body>

<header>
    <div class="subtitle">Oldenburgisches Staatstheater</div>
    <h1>ExHalle</h1>
    <div class="subtitle">Alle Vorstellungen</div>
    <button class="theme-toggle" id="themeToggle" title="Dark/Light Mode umschalten">🌙</button>
</header>

<div class="meta">
    Automatisch aktualisiert am <strong>{now_str}</strong> &nbsp;·&nbsp;
    <strong>{upcoming}</strong> kommende Termine
</div>

{timeline_html}

<main>
    {cards_html if events else '<div class="no-events">Keine Termine gefunden.</div>'}
</main>

<footer>
    Daten von <a href="https://staatstheater.de/kalender" target="_blank">staatstheater.de</a> &nbsp;·&nbsp;
    Generiert am {now_str} &nbsp;·&nbsp;
    <a href="https://litschi.space/Impressum.html" target="_blank" rel="noopener">Impressum</a>
    &nbsp;·&nbsp;
    <a href="https://litschi.space/report.html?page=exerzierhalle.de" target="_blank" rel="noopener" class="bug-report-link">🐛 Fehler melden</a>
</footer>

<script>
// Theme Toggle
(function() {{
    const html = document.documentElement;
    const btn = document.getElementById('themeToggle');
    const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

    const saved = localStorage.getItem('theme');
    const isDark = saved ? saved === 'dark' : systemDark;
    if (saved) html.setAttribute('data-theme', saved);
    btn.textContent = isDark ? '☀️' : '🌙';

    btn.addEventListener('click', () => {{
        const current = html.getAttribute('data-theme') ||
            (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
        const next = current === 'dark' ? 'light' : 'dark';
        html.setAttribute('data-theme', next);
        localStorage.setItem('theme', next);
        btn.textContent = next === 'dark' ? '☀️' : '🌙';
    }});
}})();
</script>

<script>
(function() {{
    const main = document.querySelector('main');
    const cards = Array.from(main.querySelectorAll(':scope > .event-card'));
    if (cards.length === 0) return;

    function getTitel(card) {{
        const el = card.querySelector('.event-titel');
        return el ? el.textContent.trim().toLowerCase() : '';
    }}

    const groups = [];
    let i = 0;
    while (i < cards.length) {{
        const titel = getTitel(cards[i]);
        const group = [cards[i]];
        let j = i + 1;
        while (j < cards.length && getTitel(cards[j]) === titel) {{
            group.push(cards[j]);
            j++;
        }}
        groups.push(group);
        i = j;
    }}

    groups.forEach(group => {{
        if (group.length < 2) return;
        const count = group.length;

        const wrapper = document.createElement('div');
        wrapper.className = 'card-stack';
        wrapper.dataset.stackSize = count;
        main.insertBefore(wrapper, group[0]);

        const topWrap = document.createElement('div');
        topWrap.className = 'card-stack-top' + (group[0].classList.contains('premiere') ? ' premiere-stack' : '');
        topWrap.appendChild(group[0]);
        wrapper.appendChild(topWrap);

        const btn = document.createElement('button');
        btn.className = 'stack-toggle';
        btn.innerHTML = `<i class="toggle-arrow">▼</i> ${{count - 1}} weitere${{count - 1 !== 1 ? ' Termine' : 'n Termin'}} anzeigen`;
        wrapper.appendChild(btn);

        const rest = document.createElement('div');
        rest.className = 'card-stack-rest';
        for (let k = 1; k < group.length; k++) {{
            rest.appendChild(group[k]);
        }}
        wrapper.appendChild(rest);

        btn.addEventListener('click', () => {{
            const isOpen = wrapper.classList.toggle('is-open');
            btn.innerHTML = isOpen
                ? `<i class="toggle-arrow">▼</i> Termine einklappen`
                : `<i class="toggle-arrow">▼</i> ${{count - 1}} weitere${{count - 1 !== 1 ? ' Termine' : 'n Termin'}} anzeigen`;
        }});
    }});
}})();
</script>

<script>
// Zeitstrahl: zentrieren oder zu Heute scrollen
(function() {{
    const bar   = document.querySelector('.timeline-bar');
    const inner = document.getElementById('tlInner');
    const now   = document.getElementById('tlNow');
    if (!bar || !inner) return;

    function alignTimeline() {{
        const barW   = bar.clientWidth;
        const innerW = inner.offsetWidth;   // offsetWidth = echte Inhaltsbreite bei width:max-content

        if (innerW <= barW) {{
            // Inhalt kürzer als Viewport → mittig ausrichten
            inner.style.marginLeft = Math.max(0, Math.floor((barW - innerW) / 2)) + 'px';
        }} else {{
            inner.style.marginLeft = '';
            if (now) {{
                // Inhalt breiter → Heute in die Mitte scrollen
                const seg = now.closest('.tl-seg');
                if (seg) {{
                    bar.scrollLeft = Math.max(0, seg.offsetLeft + now.offsetLeft - barW / 2);
                }}
            }}
        }}
    }}

    // Nach Layout-Berechnung ausführen
    requestAnimationFrame(alignTimeline);
    window.addEventListener('resize', alignTimeline);
}})();
</script>

</body>
</html>"""


def main():
    all_events = []
    months = get_months_to_scrape()

    print(f"Scrape {len(months)} Monate: {', '.join(months)}")

    for monat in months:
        print(f"  Lade Monat {monat[:2]}/{monat[2:]}...", end=" ")
        soup = fetch_calendar_page(monat)
        if soup:
            events = parse_events(soup, monat)
            print(f"{len(events)} ExHalle-Termine gefunden")
            all_events.extend(events)
        else:
            print("Fehler")

    # Duplikate entfernen (gleicher Titel + Datum + Uhrzeit)
    seen = set()
    unique_events = []
    for e in all_events:
        key = (e.get("datum_iso"), e["titel"], e.get("uhrzeit") or "")
        if key not in seen:
            seen.add(key)
            unique_events.append(e)

    print(f"\nGesamt: {len(unique_events)} ExHalle-Termine (dedupliziert)")

    # HTML generieren
    html = generate_html(unique_events)

    output_file = "exhalle_termine.html"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"HTML-Seite gespeichert: {output_file}")

    # Auch als JSON speichern
    json_file = "exhalle_termine.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(unique_events, f, ensure_ascii=False, indent=2)
    print(f"JSON-Daten gespeichert:  {json_file}")


if __name__ == "__main__":
    main()
