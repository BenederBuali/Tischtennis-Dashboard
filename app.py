"""
TT Dashboard – Flask Web-App
============================
Scrapt XTTV automatisch alle 4 Stunden und stellt das Dashboard
als Website bereit. Läuft auf Railway (kostenlos).
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import re
import threading
import time
from datetime import datetime
from flask import Flask, render_template, jsonify

app = Flask(__name__)

# ─── Konfiguration ─────────────────────────────────────────────────────────────

LIGA_ID_DEFAULT = 8297          # Bekannte Liga-ID als Startpunkt
BASE_URL    = "https://oettv.xttv.at/ed/index.php"
TEAM_KÜRZEL = "SWER"
ENCODING    = "iso-8859-1"
UPDATE_INTERVALL_STUNDEN = 4
LIGA_ID_SUCHBEREICH = 30        # Wie viele IDs rund um die bekannte ID durchsuchen

VERLAUF_PFAD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "tt_verlauf.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36"
}

# ─── Globaler Daten-Cache ───────────────────────────────────────────────────────

_cache = {
    "tabelle":    [],
    "rangliste":  [],
    "vergangene": [],
    "kuenftige":  [],
    "verlauf":    [],
    "zuletzt":    None,
    "fehler":     None,
    "liga_id":    LIGA_ID_DEFAULT,
    "liga_name":  "",
}
_cache_lock = threading.Lock()

# ─── Scraping-Hilfsfunktionen ──────────────────────────────────────────────────

def fetch(url: str, params: dict = None) -> BeautifulSoup:
    r = requests.get(url, params=params, headers=HEADERS, timeout=15)
    r.encoding = ENCODING
    return BeautifulSoup(r.text, "html.parser")

def safe_text(el) -> str:
    if el is None:
        return ""
    return el.get_text(separator=" ", strip=True)

# ─── Scraper ────────────────────────────────────────────────────────────────────

def finde_aktuelle_liga_id(bekannte_id: int) -> tuple:
    """
    Prüft ob SWER noch in der bekannten Liga ist.
    Falls nicht: sucht in benachbarten IDs (±LIGA_ID_SUCHBEREICH).
    Gibt (liga_id, liga_name) zurück.
    """
    def enthaelt_swer(lid: int) -> tuple:
        try:
            soup = fetch(BASE_URL, {"lid": lid})
            for row in soup.find_all("tr"):
                zellen = row.find_all("td")
                if len(zellen) < 4:
                    continue
                if zellen[1].get("data-msrangsort"):
                    kürzel = safe_text(zellen[3]).strip()
                    if TEAM_KÜRZEL.upper() in kürzel.upper():
                        # Liga-Namen aus Seitentitel lesen
                        titel = soup.find("title")
                        name = safe_text(titel).strip() if titel else str(lid)
                        return True, name
        except Exception:
            pass
        return False, ""

    # Zuerst bekannte ID prüfen
    gefunden, name = enthaelt_swer(bekannte_id)
    if gefunden:
        return bekannte_id, name

    print(f"SWER nicht in Liga {bekannte_id} – suche in benachbarten IDs...")
    for offset in range(1, LIGA_ID_SUCHBEREICH + 1):
        for kandidat in [bekannte_id + offset, bekannte_id - offset]:
            gefunden, name = enthaelt_swer(kandidat)
            if gefunden:
                print(f"SWER gefunden in Liga {kandidat}: {name}")
                return kandidat, name

    print("SWER in keiner benachbarten Liga gefunden – behalte bekannte ID")
    return bekannte_id, ""


def lade_ligatabelle(liga_id: int = LIGA_ID_DEFAULT) -> list:
    """
    Mannschaftstabelle scrapen.
    XTTV-Struktur (15 Zellen pro Teamzeile, keine Links in der Zeile):
      td[0] img (Aufsteiger/Absteiger Icon)
      td[1] Rang ("1.")  ← hat data-msrangsort Attribut
      td[2] Mannschaftsname
      td[3] Kürzel (z.B. "SWER2")
      td[4] Sp  td[5] S  td[6] U  td[7] N
      td[8] Sp-V+  td[9] ":"  td[10] Sp-V-
      td[11] Sz-V+  td[12] ":"  td[13] Sz-V-
      td[14] P
    """
    soup = fetch(BASE_URL, {"lid": liga_id})
    tabelle = []

    for row in soup.find_all("tr"):
        zellen = row.find_all("td")
        if len(zellen) < 15:
            continue

        # Teamzeile erkennen: td[1] hat data-msrangsort Attribut
        rang_cell = zellen[1]
        if not rang_cell.get("data-msrangsort"):
            continue

        rang_text = safe_text(rang_cell).strip().rstrip(".")
        if not rang_text.isdigit():
            continue
        rang = int(rang_text)

        name   = safe_text(zellen[2]).strip()
        kürzel = safe_text(zellen[3]).strip()
        if not kürzel:
            continue

        def to_int(zelle):
            t = safe_text(zelle).strip()
            return int(t) if t.isdigit() else 0

        tabelle.append({
            "rang":     rang,
            "name":     name,
            "kürzel":   kürzel,
            "sp":       to_int(zellen[4]),
            "s":        to_int(zellen[5]),
            "u":        to_int(zellen[6]),
            "n":        to_int(zellen[7]),
            "p":        to_int(zellen[14]),
            "ist_swer": TEAM_KÜRZEL.upper() in kürzel.upper() or
                        TEAM_KÜRZEL.upper() in name.upper(),
        })

    return sorted(tabelle, key=lambda x: x["rang"])

def lade_einzelrangliste(liga_id: int = LIGA_ID_DEFAULT) -> list:
    """
    Einzelrangliste scrapen – gewertete UND nicht-gewertete Spieler.

    XTTV-Struktur:
    - Gewertete Spieler: 1. TD = "N." (Rang), dann Spieler-Link, PassNr, Verein-Link,
                         Einsätze, Siege, ":", Niederlagen, RC-Link, ±, Abweichung, AK
    - Nicht-gewertete:   1. TD ist LEER oder enthält kein Rang-Muster,
                         aber Zeile hat trotzdem einen spid= Link
    """
    soup = fetch(BASE_URL, {"lid": liga_id})
    spieler = []
    rang_fake = 9000  # Für nicht-gewertete: hoher Fake-Rang zum Sortieren

    for row in soup.find_all("tr"):
        # Muss Spieler-Link haben
        spieler_link = row.find("a", href=lambda h: h and "spid=" in h and "uebersicht=" in h)
        if not spieler_link:
            continue

        name = safe_text(spieler_link)
        if not name or len(name) < 3:
            continue

        zellen = row.find_all("td")
        texts  = [safe_text(z) for z in zellen]
        if not texts:
            continue

        # Rang bestimmen
        rang_text  = texts[0].strip().rstrip(".")
        nicht_gewertet = False
        if rang_text.isdigit():
            rang = int(rang_text)
        else:
            # Nicht-gewertet: erste Zelle leer oder kein Rang
            nicht_gewertet = True
            rang = rang_fake
            rang_fake += 1

        # Verein
        verein_link = row.find("a", href=lambda h: h and "tid=" in (h or "") and "do=spiele" not in (h or ""))
        verein = safe_text(verein_link) if verein_link else ""

        # Einsätze: 5. Zelle (Index 4), muss reine Zahl sein
        einsätze = 0
        if len(texts) > 4 and texts[4].isdigit():
            einsätze = int(texts[4])

        # Siege und Niederlagen: Zelle mit ":" dazwischen
        s, n = 0, 0
        for i, t in enumerate(texts):
            if t.strip() == ":" and 0 < i < len(texts) - 1:
                try:
                    s = int(texts[i - 1])
                    n = int(texts[i + 1])
                    break
                except ValueError:
                    pass

        # RC-Rating + Player-ID: Link zu ratingscentral
        rc = ""
        rc_player_id = ""
        rc_link = row.find("a", href=lambda h: h and "ratingscentral" in (h or ""))
        if rc_link:
            rc_text = rc_link.get_text(strip=True)
            if rc_text.isdigit() and len(rc_text) in (3, 4):
                rc = rc_text
            pid_m = re.search(r"PlayerID=(\d+)", rc_link.get("href", ""))
            if pid_m:
                rc_player_id = pid_m.group(1)
        # Fallback: erste 3-4-stellige Zahl > 500 in den Zellen
        if not rc:
            for t in texts:
                t2 = t.strip()
                if t2.isdigit() and 3 <= len(t2) <= 4 and int(t2) > 500:
                    rc = t2
                    break

        spieler.append({
            "rang":           rang,
            "name":           name,
            "verein":         verein,
            "einsätze":       einsätze,
            "siege":          s,
            "niederl":        n,
            "rc":             rc,
            "rc_player_id":   rc_player_id,
            "ist_swer":       verein.upper() == TEAM_KÜRZEL.upper(),
            "ist_ich":        False,
            "win_pct":        round(s / (s + n) * 100, 1) if (s + n) > 0 else 0.0,
            "nicht_gewertet": nicht_gewertet,
        })

    return spieler


def _parse_spiele_seite(soup, alle: list):
    for item in soup.select("li, tr"):
        text = safe_text(item)
        m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})", text)
        if not m:
            continue
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d.%m.%Y %H:%M")
        except ValueError:
            continue
        mm = re.search(r"([A-Z]{3,5}\d?)\s*-\s*([A-Z]{3,5}\d?)", text)
        if not mm:
            continue
        heim, gast = mm.group(1), mm.group(2)
        rest = text[mm.end():]
        erg = re.search(r"\b(\d{1,2}):(\d{1,2})\b", rest)
        ergebnis = f"{erg.group(1)}:{erg.group(2)}" if erg else ""
        swer = TEAM_KÜRZEL.lower() in heim.lower() or TEAM_KÜRZEL.lower() in gast.lower()
        alle.append({
            "datum": dt.strftime("%a %d.%m.%Y"),
            "zeit":  m.group(2),
            "heim":  heim,
            "gast":  gast,
            "ergebnis": ergebnis,
            "swer":  swer,
            "ts":    dt.timestamp(),
            "_dt":   dt,
        })


def lade_spiele(liga_id: int = LIGA_ID_DEFAULT) -> tuple:
    alle  = []
    jetzt = datetime.now()
    params_base = {"do": "spiele", "lid": liga_id, "zeit": "alle"}
    soup = fetch(BASE_URL, params_base)
    _parse_spiele_seite(soup, alle)
    # Pagination erkennen: "Seite 1 von X"
    seiten_text = soup.get_text()
    pm = re.search(r"Seite\s+\d+\s+von\s+(\d+)", seiten_text)
    if pm:
        gesamt = int(pm.group(1))
        for seite in range(2, gesamt + 1):
            soup2 = fetch(BASE_URL, {**params_base, "seite": seite})
            _parse_spiele_seite(soup2, alle)
    # Deduplizieren
    seen, unique = set(), []
    for s in alle:
        key = (s["datum"], s["heim"], s["gast"])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    unique.sort(key=lambda x: x["ts"])
    vergangene = [s for s in unique if s["_dt"] < jetzt]
    kuenftige  = [s for s in unique if s["_dt"] >= jetzt]
    for s in unique:
        del s["_dt"]
    return vergangene, kuenftige

# ─── Verlauf ────────────────────────────────────────────────────────────────────

def lade_verlauf() -> list:
    if not os.path.exists(VERLAUF_PFAD):
        return []
    try:
        with open(VERLAUF_PFAD, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def lade_rc_history(rc_player_id: str, name: str) -> list:
    """Historische RC-Werte von RatingsCentral.com scrapen."""
    if not rc_player_id:
        return []
    try:
        r = requests.get(
            "https://www.ratingscentral.com/PlayerHistory.php",
            params={"PlayerID": rc_player_id},
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")
        eintraege = []
        for row in soup.find_all("tr"):
            zellen = row.find_all("td")
            if len(zellen) < 5:
                continue
            datum_text = safe_text(zellen[0]).strip()
            if not re.match(r"\d{4}-\d{2}-\d{2}$", datum_text):
                continue
            rating_text = safe_text(zellen[4]).strip()
            rm = re.match(r"(\d+)", rating_text)
            if not rm:
                continue
            try:
                datum_dt = datetime.strptime(datum_text, "%Y-%m-%d")
            except ValueError:
                continue
            eintraege.append({
                "name":    name,
                "datum":   datum_dt.strftime("%d.%m.%Y"),
                "zeit":    "00:00",
                "rc":      int(rm.group(1)),
                "siege":   0,
                "niederl": 0,
                "ts":      datum_dt.timestamp(),
            })
        return sorted(eintraege, key=lambda x: x["ts"])
    except Exception as e:
        print(f"RC-History Fehler ({name}): {e}")
        return []


def backfill_verlauf(verlauf: list, spieler: list) -> list:
    """Für SWER-Spieler ohne Verlauf historische Daten von RatingsCentral holen."""
    geaendert = False
    for sp in spieler:
        if not sp.get("ist_swer") or not sp.get("rc_player_id"):
            continue
        eigene = [e for e in verlauf if e.get("name") == sp["name"]]
        if len(eigene) >= 3:
            continue  # genug Daten vorhanden
        print(f"  Backfill RC-History für {sp['name']} (ID={sp['rc_player_id']})...")
        historisch = lade_rc_history(sp["rc_player_id"], sp["name"])
        if not historisch:
            continue
        existierende_ts = {e["ts"] for e in verlauf if e.get("name") == sp["name"]}
        neu = [h for h in historisch if h["ts"] not in existierende_ts]
        verlauf.extend(neu)
        geaendert = True
        print(f"  → {len(neu)} Einträge hinzugefügt")
    if geaendert:
        verlauf.sort(key=lambda x: x["ts"])
        try:
            with open(VERLAUF_PFAD, "w", encoding="utf-8") as f:
                json.dump(verlauf, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Verlauf-Speicherfehler nach Backfill: {e}")
    return verlauf


def speichere_verlauf_spieler(verlauf: list, name: str, rc: str, siege: int, niederl: int) -> list:
    """RC-Verlauf pro Spieler speichern."""
    if not rc or not rc.isdigit():
        return verlauf
    jetzt  = datetime.now()
    rc_int = int(rc)
    # Letzten Eintrag dieses Spielers suchen
    eigene = [e for e in verlauf if e.get("name") == name]
    if eigene:
        letzter = eigene[-1]
        letztes_dt = datetime.strptime(
            letzter["datum"] + " " + letzter["zeit"], "%d.%m.%Y %H:%M"
        )
        diff_h = (jetzt - letztes_dt).total_seconds() / 3600
        if letzter["rc"] == rc_int and diff_h < 6:
            return verlauf
    verlauf.append({
        "name":    name,
        "datum":   jetzt.strftime("%d.%m.%Y"),
        "zeit":    jetzt.strftime("%H:%M"),
        "rc":      rc_int,
        "siege":   siege,
        "niederl": niederl,
        "ts":      jetzt.timestamp(),
    })
    try:
        with open(VERLAUF_PFAD, "w", encoding="utf-8") as f:
            json.dump(verlauf, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Verlauf-Speicherfehler: {e}")
    return verlauf


def speichere_verlauf(verlauf, rc, siege, niederl):
    """Legacy-Wrapper."""
    return verlauf

# ─── Cache-Updater ──────────────────────────────────────────────────────────────

def aktualisiere_daten():
    global _cache
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Lade Daten von XTTV...")
    try:
        # Aktuelle Liga-ID ermitteln (erkennt Auf-/Abstieg automatisch)
        with _cache_lock:
            bekannte_id = _cache["liga_id"]
        liga_id, liga_name = finde_aktuelle_liga_id(bekannte_id)

        tabelle              = lade_ligatabelle(liga_id)
        rangliste            = lade_einzelrangliste(liga_id)
        vergangene, kuenftige = lade_spiele(liga_id)
        verlauf              = lade_verlauf()

        # RC-History Backfill: fehlende Spieler von RatingsCentral nachladen
        verlauf = backfill_verlauf(verlauf, rangliste)

        # Aktuellen Stand speichern
        for sp in [s for s in rangliste if s["ist_swer"] and s["rc"]]:
            verlauf = speichere_verlauf_spieler(verlauf, sp["name"], sp["rc"], sp["siege"], sp["niederl"])

        with _cache_lock:
            _cache["tabelle"]    = tabelle
            _cache["rangliste"]  = rangliste
            _cache["vergangene"] = vergangene
            _cache["kuenftige"]  = kuenftige
            _cache["verlauf"]    = verlauf
            _cache["liga_id"]    = liga_id
            _cache["liga_name"]  = liga_name
            _cache["zuletzt"]    = datetime.now().strftime("%d.%m.%Y %H:%M")
            _cache["fehler"]     = None

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Update OK – "
              f"Liga {liga_id}, {len(tabelle)} Teams, {len(rangliste)} Spieler")

    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Fehler: {e}")
        with _cache_lock:
            _cache["fehler"] = str(e)


def hintergrund_updater():
    """Läuft als Thread und aktualisiert alle N Stunden."""
    while True:
        aktualisiere_daten()
        time.sleep(UPDATE_INTERVALL_STUNDEN * 3600)


# ─── Flask-Route ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    with _cache_lock:
        data = dict(_cache)

    rangliste  = data["rangliste"]
    verlauf    = data["verlauf"]
    swer_spieler = [s for s in rangliste if s["ist_swer"]]
    # Nächstes SWER-Spiel
    swer_kuenftige = [s for s in data["kuenftige"] if s.get("swer")]
    naechstes_swer = swer_kuenftige[0] if swer_kuenftige else None
    # SWER-Kürzel aus Tabelle ermitteln
    swer_tabelle = [t for t in data["tabelle"] if t["ist_swer"]]
    swer_kuerzel = swer_tabelle[0]["kürzel"] if swer_tabelle else TEAM_KÜRZEL
    # Alle Mannschaftskürzel aus der Tabelle für Auswahl
    alle_kuerzel = json.dumps([{"k": t["kürzel"], "n": t["name"]} for t in data["tabelle"]])

    # Umlaute im Template-Kontext sicher machen (einsätze → einsaetze)
    for s in rangliste:
        s["einsaetze"] = s.get("einsätze", 0)
    for s in swer_spieler:
        s["einsaetze"] = s.get("einsätze", 0)

    # Verlauf-Kennzahlen
    letzter_rc    = verlauf[-1]["rc"] if verlauf else "–"
    vorheriger_rc = verlauf[-2]["rc"] if len(verlauf) >= 2 else (letzter_rc if letzter_rc != "–" else 0)
    rc_diff = (letzter_rc - vorheriger_rc) if isinstance(letzter_rc, int) else 0
    rc_diff_str   = (f"+{rc_diff}" if rc_diff > 0 else str(rc_diff)) if rc_diff != 0 else "±0"
    rc_diff_color = "#4ade80" if rc_diff > 0 else "#f87171" if rc_diff < 0 else "#6b7280"
    erster_eintrag = verlauf[0]["datum"] if verlauf else "–"

    # Chart-Daten
    chart_namen   = json.dumps([s["name"].split()[0] for s in swer_spieler])
    chart_siege   = json.dumps([s["siege"]   for s in swer_spieler])
    chart_niederl = json.dumps([s["niederl"] for s in swer_spieler])
    verlauf_labels  = json.dumps([e["datum"]   for e in verlauf])
    verlauf_rc_js   = json.dumps([e["rc"]      for e in verlauf])
    verlauf_siege   = json.dumps([e["siege"]   for e in verlauf])
    verlauf_niederl = json.dumps([e["niederl"] for e in verlauf])
    verlauf_alle_js = json.dumps(verlauf)
    alle_spiele_js  = json.dumps(data["vergangene"] + data["kuenftige"])

    return render_template(
        "index.html",
        tabelle       = data["tabelle"],
        rangliste     = rangliste,
        swer_spieler  = swer_spieler,
        vergangene    = data["vergangene"],
        kuenftige     = data["kuenftige"],
        verlauf       = verlauf,
        zuletzt       = data["zuletzt"] or "noch nicht geladen",
        fehler        = data["fehler"],
        letzter_rc    = letzter_rc,
        rc_diff_str   = rc_diff_str,
        rc_diff_color = rc_diff_color,
        erster_eintrag = erster_eintrag,
        chart_namen   = chart_namen,
        chart_siege   = chart_siege,
        chart_niederl = chart_niederl,
        verlauf_labels  = verlauf_labels,
        verlauf_rc      = verlauf_rc_js,
        verlauf_alle    = verlauf_alle_js,
        alle_spiele     = alle_spiele_js,
        alle_kuerzel    = alle_kuerzel,
        alle_kuerzel_list = data['tabelle'],
        naechstes_swer  = naechstes_swer,
        swer_kuerzel    = swer_kuerzel,
        verlauf_siege   = verlauf_siege,
        verlauf_niederl = verlauf_niederl,
    )



@app.route("/debug")
def debug():
    """Zeigt rohe Scraper-Ergebnisse zum Debuggen."""
    with _cache_lock:
        tabelle    = _cache["tabelle"]
        rangliste  = _cache["rangliste"]
        vergangene = _cache["vergangene"]
        kuenftige  = _cache["kuenftige"]
    lines = ["<pre style='font-family:monospace; font-size:12px;'>"]
    lines.append(f"=== LIGATABELLE ({len(tabelle)} Eintraege) ===\n")
    for t in tabelle:
        lines.append(f"  {t['rang']}. {t['kürzel']:8} {t['name'][:30]:30} Sp={t['sp']} S={t['s']} U={t['u']} N={t['n']} P={t['p']}\n")
    lines.append(f"\n=== RANGLISTE ({len(rangliste)} Spieler) ===\n")
    for s in rangliste[:40]:
        ng = " [n.g.]" if s['nicht_gewertet'] else ""
        lines.append(f"  {str(s['rang']):5} {s['name'][:25]:25} {s['verein']:6} {s['siege']}:{s['niederl']} RC={s['rc']}{ng}\n")
    lines.append(f"\n=== VERGANGENE SPIELE ({len(vergangene)}) – letzte 10 ===\n")
    for sp in vergangene[-10:]:
        lines.append(f"  {sp['datum']} {sp['zeit']}  heim='{sp['heim']}'  gast='{sp['gast']}'  {sp['ergebnis']}\n")
    lines.append(f"\n=== KUENFTIGE SPIELE ({len(kuenftige)}) ===\n")
    for sp in kuenftige[:10]:
        lines.append(f"  {sp['datum']} {sp['zeit']}  heim='{sp['heim']}'  gast='{sp['gast']}'\n")
    lines.append("</pre>")
    return "".join(lines)

@app.route("/api/status")
def status():
    with _cache_lock:
        return jsonify({
            "zuletzt":  _cache["zuletzt"],
            "spieler":  len(_cache["rangliste"]),
            "fehler":   _cache["fehler"],
        })




# ─── Start ──────────────────────────────────────────────────────────────────────

_thread_gestartet = False
_thread_lock = threading.Lock()

def starte_hintergrund_thread():
    """Thread einmalig starten – thread-safe."""
    global _thread_gestartet
    with _thread_lock:
        if not _thread_gestartet:
            _thread_gestartet = True
            t = threading.Thread(target=hintergrund_updater, daemon=True)
            t.start()


@app.before_request
def sicherstelle_thread():
    """Beim ersten Request den Updater-Thread starten (funktioniert mit gunicorn)."""
    starte_hintergrund_thread()


if __name__ == "__main__":
    starte_hintergrund_thread()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
