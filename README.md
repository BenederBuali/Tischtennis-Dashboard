# TT Dashboard – ASKö Schwertberg

Live-Dashboard für die XTTV Liga 631 Bezirksklasse Steyr Umg. / PE.

## Deploy auf Railway (kostenlos)

### 1. Diesen Ordner auf GitHub pushen

```bash
git init
git add .
git commit -m "TT Dashboard"
git branch -M main
git remote add origin https://github.com/DEIN-USERNAME/tt-dashboard.git
git push -u origin main
```

### 2. Railway einrichten

1. Gehe auf https://railway.app und melde dich mit GitHub an
2. Klicke **"New Project"** → **"Deploy from GitHub repo"**
3. Wähle dein `tt-dashboard` Repository
4. Railway erkennt automatisch das Procfile und deployt die App
5. Unter **"Settings" → "Domains"** eine öffentliche URL generieren

Fertig! Die URL kannst du auf jedem Gerät aufrufen.

## Lokales Testen

```bash
pip install -r requirements.txt
python app.py
```

Dann http://localhost:5000 im Browser öffnen.
