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
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# ─── Konfiguration ─────────────────────────────────────────────────────────────

LIGA_ID     = 8297
BASE_URL    = "https://oettv.xttv.at/ed/index.php"
TEAM_KÜRZEL = "SWER"
MEIN_NAME   = "Beneder Nevio"
ENCODING    = "iso-8859-1"
UPDATE_INTERVALL_STUNDEN = 4

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

def lade_ligatabelle() -> list:
    soup = fetch(BASE_URL, {"lid": LIGA_ID})
    tabelle = []
    for row in soup.find_all("tr"):
        zellen = row.find_all("td")
        if not zellen:
            continue
        texts = [safe_text(z) for z in zellen]
        erste = texts[0].strip().rstrip(".")
        if not erste.isdigit():
            continue
        links = row.find_all("a")
        name   = safe_text(links[0]) if links else ""
        kürzel = safe_text(links[1]) if len(links) > 1 else ""
        if not name:
            continue
        nums = [int(t) for t in texts[2:] if t.strip().isdigit()]
        if len(nums) < 5:
            continue
        tabelle.append({
            "rang":     int(erste),
            "name":     name,
            "kürzel":   kürzel,
            "sp":       nums[0],
            "s":        nums[1],
            "u":        nums[2],
            "n":        nums[3],
            "p":        nums[-1],
            "ist_swer": TEAM_KÜRZEL.upper() in kürzel.upper() or
                        TEAM_KÜRZEL.upper() in name.upper(),
        })
    return tabelle


def lade_einzelrangliste() -> list:
    soup = fetch(BASE_URL, {"lid": LIGA_ID})
    spieler = []
    for row in soup.find_all("tr"):
        zellen = row.find_all("td")
        if not zellen:
            continue
        texts = [safe_text(z) for z in zellen]
        rang_text = texts[0].strip().rstrip(".")
        if not rang_text.isdigit():
            continue
        spieler_link = row.find("a", href=lambda h: h and "spid=" in h and "uebersicht=" in h)
        if not spieler_link:
            continue
        name = safe_text(spieler_link)
        if not name:
            continue
        verein_link = row.find("a", href=lambda h: h and "tid=" in (h or ""))
        verein = safe_text(verein_link) if verein_link else ""
        try:
            einsätze = int(texts[4]) if len(texts) > 4 and texts[4].isdigit() else 0
        except ValueError:
            einsätze = 0
        s, n = 0, 0
        for i, t in enumerate(texts):
            if t == ":" and 0 < i < len(texts) - 1:
                try:
                    s = int(texts[i - 1])
                    n = int(texts[i + 1])
                    break
                except ValueError:
                    pass
        rc = ""
        rc_link = row.find("a", href=lambda h: h and "ratingscentral" in (h or ""))
        if rc_link:
            rc_text = safe_text(rc_link)
            if rc_text.isdigit():
                rc = rc_text
        if not rc:
            for t in texts:
                if t.isdigit() and len(t) == 4 and int(t) > 500:
                    rc = t
                    break
        spieler.append({
            "rang":     int(rang_text),
            "name":     name,
            "verein":   verein,
            "einsätze": einsätze,
            "siege":    s,
            "niederl":  n,
            "rc":       rc,
            "ist_swer": verein.upper() == TEAM_KÜRZEL.upper(),
            "ist_ich":  name.strip() == MEIN_NAME.strip(),
            "win_pct":  round(s / (s + n) * 100, 1) if (s + n) > 0 else 0.0,
        })
    return spieler


def lade_spiele() -> tuple:
    alle  = []
    jetzt = datetime.now()
    soup  = fetch(BASE_URL, {"do": "spiele", "lid": LIGA_ID, "zeit": "alle"})
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
        erg = re.search(r"\b(\d+):(\d+)\b", text)
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

def speichere_verlauf(verlauf: list, rc: str, siege: int, niederl: int) -> list:
    if not rc or not rc.isdigit():
        return verlauf
    jetzt  = datetime.now()
    rc_int = int(rc)
    if verlauf:
        letzter = verlauf[-1]
        letztes_dt = datetime.strptime(
            letzter["datum"] + " " + letzter["zeit"], "%d.%m.%Y %H:%M"
        )
        diff_h = (jetzt - letztes_dt).total_seconds() / 3600
        if letzter["rc"] == rc_int and diff_h < 6:
            return verlauf
    verlauf.append({
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

# ─── Cache-Updater ──────────────────────────────────────────────────────────────

def aktualisiere_daten():
    global _cache
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Lade Daten von XTTV...")
    try:
        tabelle              = lade_ligatabelle()
        rangliste            = lade_einzelrangliste()
        vergangene, kuenftige = lade_spiele()
        verlauf              = lade_verlauf()

        ich = next((s for s in rangliste if s["ist_ich"]), None)
        if ich:
            verlauf = speichere_verlauf(verlauf, ich["rc"], ich["siege"], ich["niederl"])

        with _cache_lock:
            _cache["tabelle"]    = tabelle
            _cache["rangliste"]  = rangliste
            _cache["vergangene"] = vergangene
            _cache["kuenftige"]  = kuenftige
            _cache["verlauf"]    = verlauf
            _cache["zuletzt"]    = datetime.now().strftime("%d.%m.%Y %H:%M")
            _cache["fehler"]     = None

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Update OK – "
              f"{len(tabelle)} Teams, {len(rangliste)} Spieler")

    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Fehler: {e}")
        with _cache_lock:
            _cache["fehler"] = str(e)


def hintergrund_updater():
    """Läuft als Thread und aktualisiert alle N Stunden."""
    while True:
        aktualisiere_daten()
        time.sleep(UPDATE_INTERVALL_STUNDEN * 3600)


# ─── HTML-Template ──────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TT Dashboard – ASKö Schwertberg</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #0f1117; --bg2: #1a1d27; --bg3: #22263a;
      --border: #2e3248; --text: #e2e8f0; --muted: #6b7280;
      --accent: #378ADD; --green: #4ade80; --red: #f87171;
      --swer: #1e3a5f; --swer-border: #378ADD;
      --ich: #1a2f1a; --ich-border: #4ade80;
      --radius: 10px;
    }
    body { background: var(--bg); color: var(--text);
           font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }

    header { background: var(--bg2); border-bottom: 1px solid var(--border);
             padding: 14px 20px; display: flex; align-items: center;
             justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    header h1 { font-size: 17px; font-weight: 600; }
    header h1 span { color: var(--accent); }
    .update-time { font-size: 12px; color: var(--muted); }
    .refresh-btn { background: none; border: 1px solid var(--border);
                   color: var(--muted); padding: 5px 12px; border-radius: 6px;
                   cursor: pointer; font-size: 12px; }
    .refresh-btn:hover { color: var(--text); border-color: var(--accent); }

    .container { max-width: 1100px; margin: 0 auto; padding: 20px 14px; }

    .tab-bar { display: flex; gap: 4px; border-bottom: 1px solid var(--border);
               margin-bottom: 20px; overflow-x: auto; }
    .tab { padding: 8px 16px; border-radius: var(--radius) var(--radius) 0 0;
           cursor: pointer; color: var(--muted); border: 1px solid transparent;
           border-bottom: none; font-size: 13px; white-space: nowrap;
           transition: all 0.15s; user-select: none; }
    .tab:hover { color: var(--text); background: var(--bg3); }
    .tab.active { color: var(--text); background: var(--bg2); border-color: var(--border); }
    .tab-content { display: none; }
    .tab-content.active { display: block; }

    .card { background: var(--bg2); border: 1px solid var(--border);
            border-radius: var(--radius); padding: 18px; margin-bottom: 16px; }
    .card-title { font-size: 11px; font-weight: 600; text-transform: uppercase;
                  letter-spacing: 0.08em; color: var(--muted); margin-bottom: 14px; }

    .metric-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
                  gap: 10px; margin-bottom: 16px; }
    .metric { background: var(--bg3); border-radius: var(--radius); padding: 12px 14px; }
    .metric-label { font-size: 11px; color: var(--muted); margin-bottom: 3px; }
    .metric-value { font-size: 20px; font-weight: 600; }
    .metric-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }

    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; padding: 7px 10px; font-size: 11px; font-weight: 600;
         text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted);
         border-bottom: 1px solid var(--border); }
    td { padding: 8px 10px; border-bottom: 1px solid #1e2133; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: var(--bg3); }
    .swer-row td { background: var(--swer) !important; }
    .swer-row:hover td { background: #204570 !important; }
    .ich-row td { background: var(--ich) !important; }
    .ich-row:hover td { background: #1f3f1f !important; }

    .center { text-align: center; }
    .bold { font-weight: 600; }
    .mono { font-family: 'Consolas', monospace; }
    .muted { color: var(--muted); }
    .green { color: var(--green); font-weight: 600; }
    .red { color: var(--red); }

    .win-bar-wrap { position: relative; height: 16px; background: var(--bg3);
                    border-radius: 99px; min-width: 70px; overflow: hidden; }
    .win-bar-fill { position: absolute; left: 0; top: 0; bottom: 0;
                    border-radius: 99px; opacity: 0.7; }
    .win-bar-label { position: absolute; right: 5px; top: 50%;
                     transform: translateY(-50%); font-size: 11px; font-weight: 600; }

    .player-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
    .player-card { background: var(--bg3); border: 1px solid var(--border);
                   border-radius: var(--radius); padding: 12px;
                   display: flex; align-items: center; gap: 10px; }
    .ich-card { border-color: var(--ich-border); background: var(--ich); }
    .player-avatar { width: 38px; height: 38px; border-radius: 50%;
                     background: var(--swer); border: 1px solid var(--swer-border);
                     display: flex; align-items: center; justify-content: center;
                     font-weight: 700; font-size: 13px; color: var(--accent); flex-shrink: 0; }
    .player-name { font-weight: 600; font-size: 13px; }
    .player-meta { font-size: 11px; color: var(--muted); margin-top: 2px; }
    .player-stats { margin-left: auto; text-align: right; flex-shrink: 0; }
    .stat-win  { color: var(--green); font-weight: 700; }
    .stat-loss { color: var(--red); }
    .stat-sep  { color: var(--muted); margin: 0 2px; }
    .stat-pct  { display: block; font-size: 11px; color: var(--muted); margin-top: 2px; }

    .chart-wrap { position: relative; height: 260px; }

    .legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px;
              color: var(--muted); margin-top: 8px; }
    .legend span { display: flex; align-items: center; gap: 4px; }
    .legend-dot { width: 10px; height: 10px; border-radius: 2px; }

    .error-banner { background: #3a1a1a; border: 1px solid var(--red);
                    border-radius: var(--radius); padding: 14px; margin-bottom: 16px;
                    color: var(--red); font-size: 13px; }

    /* Mobile */
    @media (max-width: 600px) {
      header h1 { font-size: 14px; }
      .metric-value { font-size: 17px; }
      td, th { padding: 6px 7px; font-size: 12px; }
    }
  </style>
</head>
<body>

<header>
  <h1>🏓 TT Dashboard – <span>ASKö Schwertberg</span></h1>
  <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
    <div class="update-time">Stand: {{ zuletzt }} Uhr</div>
    <button class="refresh-btn" onclick="location.reload()">↻ Aktualisieren</button>
  </div>
</header>

<div class="container">

{% if fehler %}
<div class="error-banner">⚠ Fehler beim letzten Datenladen: {{ fehler }}</div>
{% endif %}

<div class="tab-bar">
  <div class="tab active" onclick="switchTab('uebersicht')">Übersicht</div>
  <div class="tab" onclick="switchTab('rangliste')">Rangliste</div>
  <div class="tab" onclick="switchTab('tabelle')">Ligatabelle</div>
  <div class="tab" onclick="switchTab('spiele')">Spieltermine</div>
  <div class="tab" onclick="switchTab('verlauf')">Mein Verlauf</div>
</div>

<!-- ÜBERSICHT -->
<div id="tab-uebersicht" class="tab-content active">
  <div class="card">
    <div class="card-title">SWER-Spieler in der Liga</div>
    <div class="player-grid">
      {% for s in swer_spieler %}
      <div class="player-card {% if s.ist_ich %}ich-card{% endif %}">
        <div class="player-avatar">{{ s.name.split()[0][0] }}{{ s.name.split()[-1][0] }}</div>
        <div>
          <div class="player-name">{{ s.name }}{% if s.ist_ich %} ← du{% endif %}</div>
          <div class="player-meta">Rang {{ s.rang }} · RC {{ s.rc }} · {{ s.einsaetze }} Einsätze</div>
        </div>
        <div class="player-stats">
          <span class="stat-win">{{ s.siege }}S</span>
          <span class="stat-sep">/</span>
          <span class="stat-loss">{{ s.niederl }}N</span>
          <span class="stat-pct">{{ s.win_pct }}%</span>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
  <div class="card">
    <div class="card-title">Siege & Niederlagen – SWER-Spieler</div>
    <div class="chart-wrap"><canvas id="barChart"></canvas></div>
    <div class="legend">
      <span><span class="legend-dot" style="background:#4ade80;"></span>Siege</span>
      <span><span class="legend-dot" style="background:#f87171;"></span>Niederlagen</span>
    </div>
  </div>
</div>

<!-- RANGLISTE -->
<div id="tab-rangliste" class="tab-content">
  <div class="card">
    <div class="card-title">Einzelrangliste Top 30 (SWER = blau, du = grün)</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th class="center">#</th><th>Name</th><th class="center">Verein</th>
          <th class="center">Eins.</th><th class="center">S</th><th class="center">N</th>
          <th>Win-Rate</th><th class="center">RC</th>
        </tr></thead>
        <tbody>
          {% for s in rangliste[:30] %}
          <tr class="{% if s.ist_swer %}swer-row{% endif %}{% if s.ist_ich %} ich-row{% endif %}">
            <td class="center muted">{{ s.rang }}.</td>
            <td class="bold">{{ s.name }}{% if s.ist_ich %} ←{% endif %}</td>
            <td class="center">{{ s.verein }}</td>
            <td class="center">{{ s.einsaetze }}</td>
            <td class="center green">{{ s.siege }}</td>
            <td class="center red">{{ s.niederl }}</td>
            <td>
              <div class="win-bar-wrap">
                <div class="win-bar-fill" style="width:{{ [s.win_pct, 100]|min }}%;
                  background:{% if s.win_pct >= 70 %}#4ade80{% elif s.win_pct >= 50 %}#60a5fa{% else %}#f87171{% endif %};"></div>
                <span class="win-bar-label">{{ s.win_pct }}%</span>
              </div>
            </td>
            <td class="center mono">{{ s.rc }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- LIGATABELLE -->
<div id="tab-tabelle" class="tab-content">
  <div class="card">
    <div class="card-title">Mannschaftstabelle – 631 Bezirksklasse Steyr Umg. / PE 2025/26</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th class="center">#</th><th>Mannschaft</th>
          <th class="center">Sp</th><th class="center">S</th>
          <th class="center">U</th><th class="center">N</th><th class="center">P</th>
        </tr></thead>
        <tbody>
          {% for t in tabelle %}
          <tr class="{% if t.ist_swer %}swer-row{% endif %}">
            <td class="center bold">{{ t.rang }}.</td>
            <td>{{ t.name }}{% if t.ist_swer %} ★{% endif %}</td>
            <td class="center">{{ t.sp }}</td>
            <td class="center green">{{ t.s }}</td>
            <td class="center muted">{{ t.u }}</td>
            <td class="center red">{{ t.n }}</td>
            <td class="center bold">{{ t.p }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- SPIELTERMINE -->
<div id="tab-spiele" class="tab-content">
  <div class="card">
    <div class="card-title">Nächste Spiele</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Datum</th><th>Zeit</th><th>Heim</th><th></th><th>Gast</th></tr></thead>
        <tbody>
          {% if kuenftige %}
            {% for sp in kuenftige %}
            <tr class="{% if sp.swer %}swer-row{% endif %}">
              <td class="mono">{{ sp.datum }}</td>
              <td class="mono muted">{{ sp.zeit }}</td>
              <td class="bold">{{ sp.heim }}</td>
              <td class="center muted">vs</td>
              <td class="bold">{{ sp.gast }}</td>
            </tr>
            {% endfor %}
          {% else %}
            <tr><td colspan="5" class="center muted" style="padding:1rem;">Keine kommenden Spiele gefunden</td></tr>
          {% endif %}
        </tbody>
      </table>
    </div>
  </div>
  <div class="card">
    <div class="card-title">Letzte Spiele</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Datum</th><th>Zeit</th><th>Heim</th><th></th><th>Gast</th><th class="center">Ergebnis</th></tr></thead>
        <tbody>
          {% for sp in vergangene[-15:]|reverse %}
          <tr class="{% if sp.swer %}swer-row{% endif %}">
            <td class="mono">{{ sp.datum }}</td>
            <td class="mono muted">{{ sp.zeit }}</td>
            <td class="bold">{{ sp.heim }}</td>
            <td class="center muted">vs</td>
            <td class="bold">{{ sp.gast }}</td>
            <td class="center bold mono">{{ sp.ergebnis }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- MEIN VERLAUF -->
<div id="tab-verlauf" class="tab-content">
  <div class="metric-row">
    <div class="metric">
      <div class="metric-label">Aktueller RC</div>
      <div class="metric-value" style="color:var(--accent);">{{ letzter_rc }}</div>
      <div class="metric-sub">Rating Central</div>
    </div>
    <div class="metric">
      <div class="metric-label">Letzte Änderung</div>
      <div class="metric-value" style="color:{{ rc_diff_color }};">{{ rc_diff_str }}</div>
      <div class="metric-sub">seit letztem Eintrag</div>
    </div>
    <div class="metric">
      <div class="metric-label">Einträge</div>
      <div class="metric-value">{{ verlauf|length }}</div>
      <div class="metric-sub">seit erstem Start</div>
    </div>
    <div class="metric">
      <div class="metric-label">Erster Eintrag</div>
      <div class="metric-value" style="font-size:15px;">{{ erster_eintrag }}</div>
    </div>
  </div>
  <div class="card">
    <div class="card-title">RC-Rating Verlauf</div>
    <div class="chart-wrap" style="height:280px;"><canvas id="verlaufChart"></canvas></div>
  </div>
  <div class="card">
    <div class="card-title">Siege & Niederlagen Verlauf</div>
    <div class="chart-wrap" style="height:220px;"><canvas id="snChart"></canvas></div>
  </div>
  <div class="card">
    <div class="card-title">Alle Einträge</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Datum</th><th>Zeit</th>
          <th class="center">RC</th><th class="center">Änderung</th>
          <th class="center">Siege</th><th class="center">Niederlagen</th>
        </tr></thead>
        <tbody>
          {% for e in verlauf|reverse %}
          {% set loop_idx = loop.revindex0 %}
          <tr>
            <td class="mono">{{ e.datum }}</td>
            <td class="mono muted">{{ e.zeit }}</td>
            <td class="center bold" style="color:var(--accent);">{{ e.rc }}</td>
            <td class="center bold" style="color:
              {% if verlauf|length > loop.revindex %}
                {% set prev = verlauf[verlauf|length - loop.revindex - 1] %}
                {% if e.rc > prev.rc %}#4ade80{% elif e.rc < prev.rc %}#f87171{% else %}#6b7280{% endif %}
              {% else %}#6b7280{% endif %};">
              {% if verlauf|length > loop.revindex %}
                {% set prev_rc = verlauf[verlauf|length - loop.revindex - 1].rc %}
                {% if e.rc - prev_rc > 0 %}+{{ e.rc - prev_rc }}{% elif e.rc - prev_rc < 0 %}{{ e.rc - prev_rc }}{% else %}±0{% endif %}
              {% else %}–{% endif %}
            </td>
            <td class="center green">{{ e.siege }}</td>
            <td class="center red">{{ e.niederl }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>

</div><!-- /container -->

<script>
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
}

// Balken-Chart SWER Spieler
const swernamen  = {{ chart_namen|safe }};
const swersiege  = {{ chart_siege|safe }};
const swernied   = {{ chart_niederl|safe }};

new Chart(document.getElementById('barChart'), {
  type: 'bar',
  data: {
    labels: swernamen,
    datasets: [
      { label: 'Siege',       data: swersiege, backgroundColor: 'rgba(74,222,128,0.75)', borderRadius: 4 },
      { label: 'Niederlagen', data: swernied,  backgroundColor: 'rgba(248,113,113,0.75)', borderRadius: 4 },
    ]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { color: '#6b7280' }, grid: { color: '#1e2133' } },
      y: { ticks: { color: '#6b7280' }, grid: { color: '#1e2133' } }
    }
  }
});

// Verlauf-Charts
const vLabels  = {{ verlauf_labels|safe }};
const vRC      = {{ verlauf_rc|safe }};
const vSiege   = {{ verlauf_siege|safe }};
const vNied    = {{ verlauf_niederl|safe }};

if (vLabels.length > 0) {
  new Chart(document.getElementById('verlaufChart'), {
    type: 'line',
    data: {
      labels: vLabels,
      datasets: [{ label: 'RC', data: vRC,
        borderColor: '#378ADD', backgroundColor: 'rgba(55,138,221,0.1)',
        borderWidth: 2, pointRadius: 4, tension: 0.3, fill: true }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#6b7280', maxTicksLimit: 10 }, grid: { color: '#1e2133' } },
        y: { ticks: { color: '#6b7280' }, grid: { color: '#1e2133' } }
      }
    }
  });
  new Chart(document.getElementById('snChart'), {
    type: 'line',
    data: {
      labels: vLabels,
      datasets: [
        { label: 'Siege',       data: vSiege, borderColor: '#4ade80', borderWidth: 2, pointRadius: 3, tension: 0.3, fill: false },
        { label: 'Niederlagen', data: vNied,  borderColor: '#f87171', borderWidth: 2, pointRadius: 3, tension: 0.3, fill: false },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#6b7280', maxTicksLimit: 10 }, grid: { color: '#1e2133' } },
        y: { ticks: { color: '#6b7280' }, grid: { color: '#1e2133' } }
      }
    }
  });
} else {
  document.getElementById('verlaufChart').parentElement.innerHTML =
    '<p style="color:#6b7280;text-align:center;padding:2rem;">Noch keine Daten – beim ersten Aufruf wird der erste Eintrag gespeichert.</p>';
  document.getElementById('snChart').parentElement.innerHTML = '';
}
</script>
</body>
</html>
"""

# ─── Flask-Route ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    with _cache_lock:
        data = dict(_cache)

    rangliste  = data["rangliste"]
    verlauf    = data["verlauf"]
    swer_spieler = [s for s in rangliste if s["ist_swer"]]

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

    return render_template_string(
        HTML_TEMPLATE,
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
        verlauf_siege   = verlauf_siege,
        verlauf_niederl = verlauf_niederl,
    )


@app.route("/api/status")
def status():
    with _cache_lock:
        return jsonify({
            "zuletzt":  _cache["zuletzt"],
            "spieler":  len(_cache["rangliste"]),
            "fehler":   _cache["fehler"],
        })


# ─── Start ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Beim Start sofort Daten laden, dann Hintergrund-Thread starten
    t = threading.Thread(target=hintergrund_updater, daemon=True)
    t.start()
    # Kurz warten bis erster Load durch ist
    time.sleep(2)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
