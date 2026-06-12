"""
Serveur Flask — CityScore, fiche par commune française.
"""
import json
import re
import sys
import threading
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import requests as req
from flask import Flask, render_template_string, jsonify, redirect, url_for

app = Flask(__name__)
DATA_DIR = Path(__file__).parent / "data"
sys.path.insert(0, str(Path(__file__).parent))

# ── Slug utilities ─────────────────────────────────────────────────────────────

def to_slug(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_str.lower()).strip("-")


def find_commune_dir(slug: str) -> str | None:
    """Return the directory name matching a slug, or None."""
    if DATA_DIR.exists():
        for d in DATA_DIR.iterdir():
            if d.is_dir() and to_slug(d.name) == slug:
                return d.name
    return None


def data_is_fresh(commune_name: str, max_age_days: int = 7) -> bool:
    meta = DATA_DIR / commune_name / "metadata.json"
    if meta.exists():
        info = json.loads(meta.read_text())
        collected = datetime.fromisoformat(info.get("collected_at", "2000-01-01"))
        return datetime.now() - collected < timedelta(days=max_age_days)
    return (DATA_DIR / commune_name / "geo.json").exists()


def lookup_commune_api(slug: str) -> tuple[str, dict] | tuple[None, None]:
    """Query geo.api.gouv.fr to find a commune by its slug. Returns (name, info) or (None, None)."""
    # Convert slug back to approximate search term
    q = slug.replace("-", " ")
    try:
        r = req.get(
            "https://geo.api.gouv.fr/communes",
            params={"nom": q, "fields": "nom,code,codesPostaux,departement,centre", "boost": "population", "limit": 5},
            timeout=5,
        )
        if not r.ok:
            return None, None
        results = r.json()
        # Find best match by slug
        for item in results:
            if to_slug(item["nom"]) == slug:
                name = item["nom"]
                coords = item.get("centre", {}).get("coordinates", [0, 0])
                info = {
                    "code": item["code"],
                    "cp": item.get("codesPostaux", [""])[0],
                    "dept": item.get("departement", {}).get("code", ""),
                    "lat": coords[1],
                    "lon": coords[0],
                }
                return name, info
    except Exception:
        pass
    return None, None


# ── Background collection ──────────────────────────────────────────────────────

_jobs: dict[str, str] = {}  # slug → "collecting" | "done" | "error"
_jobs_lock = threading.Lock()


def start_collection(slug: str, commune_name: str, commune_info: dict):
    with _jobs_lock:
        if _jobs.get(slug) == "collecting":
            return
        _jobs[slug] = "collecting"

    def run():
        try:
            import collect
            collect.collect_commune(commune_name, commune_info)
            meta = {"collected_at": datetime.now().isoformat()}
            (DATA_DIR / commune_name / "metadata.json").write_text(json.dumps(meta))
            with _jobs_lock:
                _jobs[slug] = "done"
        except Exception as e:
            print(f"[collect] Error for {commune_name}: {e}")
            with _jobs_lock:
                _jobs[slug] = "error"

    threading.Thread(target=run, daemon=True).start()


# ── Hardcoded list for /compare ────────────────────────────────────────────────
COMMUNES = ["Saint-Pierre-de-Chandieu", "Chaponnay", "Saint-Laurent-de-Mure"]

TEMPLATE_HOME = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CityScore — Trouvez votre futur lieu de vie</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #f0f2f5; color: #1a1a2e; min-height: 100vh; }
  .hero { background: linear-gradient(135deg, #1a1a2e 0%, #0f3460 60%, #533483 100%); color: white; padding: 80px 24px 100px; text-align: center; }
  .hero h1 { font-size: 2.8rem; font-weight: 800; letter-spacing: -1px; }
  .hero h1 span { color: #a78bfa; }
  .hero p { margin-top: 12px; opacity: 0.75; font-size: 1.05rem; }
  .search-wrap { max-width: 560px; margin: 36px auto 0; position: relative; }
  .search-wrap input { width: 100%; padding: 16px 20px; border-radius: 12px; border: none; font-size: 1rem; outline: none; box-shadow: 0 8px 30px rgba(0,0,0,0.3); }
  .search-wrap input::placeholder { color: #aaa; }
  .dropdown { position: absolute; top: calc(100% + 6px); left: 0; right: 0; background: white; border-radius: 10px; box-shadow: 0 8px 30px rgba(0,0,0,0.15); overflow: hidden; z-index: 100; display: none; }
  .dropdown.open { display: block; }
  .dropdown-item { padding: 12px 18px; cursor: pointer; border-bottom: 1px solid #f5f5f5; font-size: 0.9rem; color: #1a1a2e; }
  .dropdown-item:last-child { border-bottom: none; }
  .dropdown-item:hover { background: #f0f4ff; }
  .dropdown-item .dept { font-size: 0.78rem; color: #888; margin-left: 6px; }
  .cards { max-width: 900px; margin: -40px auto 0; padding: 0 16px 40px; }
  .cards-title { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 1px; color: #999; margin-bottom: 12px; font-weight: 600; }
  .examples { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
  .example-card { background: white; border-radius: 10px; padding: 14px 16px; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.07); transition: transform 0.15s, box-shadow 0.15s; text-decoration: none; color: inherit; display: block; }
  .example-card:hover { transform: translateY(-2px); box-shadow: 0 6px 18px rgba(0,0,0,0.12); }
  .example-card .name { font-weight: 600; font-size: 0.9rem; }
  .example-card .meta { font-size: 0.75rem; color: #888; margin-top: 4px; }
  footer { text-align: center; padding: 20px; color: #bbb; font-size: 0.78rem; }
  @media (max-width: 600px) { .hero h1 { font-size: 1.9rem; } .hero { padding: 60px 16px 80px; } }
</style>
</head>
<body>
<div class="hero">
  <h1>City<span>Score</span></h1>
  <p>Données publiques centralisées pour choisir votre lieu de vie</p>
  <div class="search-wrap">
    <input type="text" id="search" placeholder="Rechercher une commune..." autocomplete="off">
    <div class="dropdown" id="dropdown"></div>
  </div>
</div>
<div class="cards">
  <div class="cards-title">Communes récentes</div>
  <div class="examples">
    {% for c in communes %}
    <a class="example-card" href="/commune/{{ slugs[c] }}">
      <div class="name">{{ c }}</div>
      <div class="meta">{{ pops[c] }} hab. · {{ dists[c] }} km de Lyon</div>
    </a>
    {% endfor %}
  </div>
</div>
<footer>Sources : DVF · RPLS · GéoRisques · Éducation nationale · SNCF · OFGL · SIRENE · ARCEP · Open-Meteo · INSEE Melodi</footer>
<script>
const inp = document.getElementById('search');
const dd = document.getElementById('dropdown');
let timer;
inp.addEventListener('input', () => {
  clearTimeout(timer);
  const q = inp.value.trim();
  if (q.length < 2) { dd.classList.remove('open'); return; }
  timer = setTimeout(() => {
    fetch('/api/search?q=' + encodeURIComponent(q))
      .then(r => r.json())
      .then(results => {
        if (!results.length) { dd.classList.remove('open'); return; }
        dd.innerHTML = results.map(r =>
          `<div class="dropdown-item" onclick="window.location='/commune/${r.slug}'">${r.nom}<span class="dept">${r.dept}</span></div>`
        ).join('');
        dd.classList.add('open');
      });
  }, 200);
});
document.addEventListener('click', e => { if (!e.target.closest('.search-wrap')) dd.classList.remove('open'); });
inp.addEventListener('keydown', e => { if (e.key === 'Enter') {
  const first = dd.querySelector('.dropdown-item');
  if (first) first.click();
}});
</script>
</body>
</html>
"""

TEMPLATE_LOADING = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CityScore — Collecte en cours…</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #f0f2f5; color: #1a1a2e; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .box { background: white; border-radius: 16px; padding: 48px 40px; text-align: center; box-shadow: 0 4px 24px rgba(0,0,0,0.1); max-width: 420px; width: 90%; }
  .spinner { width: 48px; height: 48px; border: 4px solid #e9ecef; border-top-color: #533483; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 24px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  h2 { font-size: 1.2rem; margin-bottom: 8px; }
  p { color: #777; font-size: 0.88rem; line-height: 1.6; }
  .steps { margin-top: 20px; text-align: left; font-size: 0.82rem; color: #555; }
  .steps li { padding: 4px 0; }
</style>
</head>
<body>
<div class="box">
  <div class="spinner"></div>
  <h2>Collecte en cours…</h2>
  <p>Nous récupérons les données publiques pour <strong>{{ commune_name }}</strong>. Cela prend environ 2 minutes.</p>
  <ul class="steps">
    <li>🏠 Transactions immobilières (DVF)</li>
    <li>🏫 Établissements scolaires & IPS</li>
    <li>⚠️ Risques naturels & industriels</li>
    <li>💰 Budget & fiscalité commune</li>
    <li>✈️ Aéroports, gares, qualité de l'air…</li>
  </ul>
</div>
<script>
setInterval(() => {
  fetch('/api/status/{{ slug }}')
    .then(r => r.json())
    .then(d => {
      if (d.status === 'done') window.location.href = '/commune/{{ slug }}';
      if (d.status === 'error') document.querySelector('p').textContent = 'Une erreur est survenue lors de la collecte.';
    });
}, 3000);
</script>
</body>
</html>
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{% if communes|length == 1 %}{{ communes[0] }} — CityScore{% else %}Comparatif Communes — CityScore{% endif %}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #f0f2f5; color: #1a1a2e; }
  header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: white; padding: 24px 32px; }
  header h1 { font-size: 1.6rem; font-weight: 700; }
  header p { opacity: 0.7; margin-top: 4px; font-size: 0.9rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 460px), 1fr)); gap: 16px; padding: 16px; }
  @media (max-width: 600px) {
    header { padding: 16px; }
    header h1 { font-size: 1.2rem; }
    .grid { padding: 10px; gap: 12px; }
    .card-body { padding: 10px 12px; }
    .price-table { font-size: 0.73rem; }
    .price-table th, .price-table td { padding: 3px 4px; }
    .cat-header { padding: 10px 12px; }
  }
  .card { background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  .card-header { background: linear-gradient(135deg, #0f3460 0%, #533483 100%); color: white; padding: 16px 20px; }
  .card-header h2 { font-size: 1.1rem; }
  .card-header .sub { opacity: 0.75; font-size: 0.8rem; margin-top: 2px; }
  .card-body { padding: 0; }
  /* Catégories */
  .cat { border-bottom: 1px solid #f0f0f0; }
  .cat:last-child { border-bottom: none; }
  .cat-header { display: flex; align-items: center; gap: 8px; padding: 12px 18px; cursor: pointer; user-select: none; background: #fafafa; transition: background 0.15s; }
  .cat-header:hover { background: #f0f4ff; }
  .cat-header .cat-icon { font-size: 1rem; width: 22px; text-align: center; }
  .cat-header .cat-title { font-size: 0.8rem; font-weight: 700; color: #333; text-transform: uppercase; letter-spacing: 0.6px; flex: 1; }
  .cat-header .cat-arrow { font-size: 0.7rem; color: #aaa; transition: transform 0.2s; }
  .cat-header.open .cat-arrow { transform: rotate(180deg); }
  .cat-body { padding: 12px 18px 14px; display: none; }
  .cat-body.open { display: block; }
  .section { margin-bottom: 14px; }
  .section:last-child { margin-bottom: 0; }
  .section-title { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.8px; color: #999; margin-bottom: 6px; font-weight: 600; }
  .kv { display: flex; justify-content: space-between; align-items: flex-start; padding: 4px 0; border-bottom: 1px solid #f5f5f5; gap: 8px; }
  .kv:last-child { border-bottom: none; }
  .kv .label { color: #555; font-size: 0.81rem; flex-shrink: 0; }
  .kv .value { font-weight: 600; font-size: 0.83rem; color: #1a1a2e; text-align: right; }
  .badge { display: inline-block; padding: 2px 7px; border-radius: 999px; font-size: 0.7rem; font-weight: 600; }
  .badge-green { background: #d4edda; color: #155724; }
  .badge-red { background: #f8d7da; color: #721c24; }
  .badge-orange { background: #fff3cd; color: #856404; }
  .badge-blue { background: #cce5ff; color: #004085; }
  .badge-purple { background: #e2d9f3; color: #6f42c1; }
  .badge-teal { background: #d1ecf1; color: #0c5460; }
  .price-table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
  .price-table th { background: #f8f9fa; padding: 4px 6px; text-align: left; font-weight: 600; color: #555; border-bottom: 2px solid #e9ecef; }
  .price-table td { padding: 3px 6px; border-bottom: 1px solid #f5f5f5; }
  .price-table tr:last-child td { border-bottom: none; }
  .price-table .maison { color: #8B4513; font-weight: 500; }
  .price-table .appt { color: #1a6b8a; font-weight: 500; }
  .risk-list { display: flex; flex-wrap: wrap; gap: 4px; }
  .risk-tag { background: #fff3cd; color: #856404; padding: 2px 7px; border-radius: 4px; font-size: 0.71rem; }
  .risk-tag.industrial { background: #f8d7da; color: #721c24; font-weight: 600; }
  .school-item { padding: 5px 0; border-bottom: 1px solid #f5f5f5; }
  .school-item:last-child { border-bottom: none; }
  .school-name { font-size: 0.82rem; font-weight: 500; }
  .school-meta { font-size: 0.71rem; color: #777; margin-top: 2px; display: flex; flex-wrap: wrap; gap: 4px; }
  .row2 { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #f5f5f5; font-size: 0.81rem; }
  .row2:last-child { border-bottom: none; }
  .ips-bar-wrap { display: flex; align-items: center; gap: 8px; margin-top: 2px; }
  .ips-bar-bg { flex: 1; height: 5px; background: #e9ecef; border-radius: 3px; }
  .ips-bar { height: 5px; border-radius: 3px; background: linear-gradient(90deg, #ff6b6b, #ffd93d, #6bcb77); }
  .ips-ref { font-size: 0.68rem; color: #aaa; }
  .chips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 4px; }
  .chip { background: #f0f4ff; color: #333; border-radius: 6px; padding: 3px 9px; font-size: 0.75rem; display: flex; align-items: center; gap: 4px; }
  .chip .num { font-weight: 700; color: #0f3460; }
  footer { text-align: center; padding: 20px; color: #aaa; font-size: 0.8rem; }
  /* CityScore */
  .score-banner { padding: 14px 18px 10px; border-bottom: 1px solid #f0f0f0; background: #fafbff; }
  .score-global-row { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
  .score-circle { width: 52px; height: 52px; border-radius: 50%; display: flex; flex-direction: column; align-items: center; justify-content: center; flex-shrink: 0; }
  .score-circle .num { font-size: 1.2rem; font-weight: 800; line-height: 1; }
  .score-circle .lbl { font-size: 0.55rem; font-weight: 600; text-transform: uppercase; opacity: 0.85; }
  .score-green { background: #d4edda; color: #155724; }
  .score-orange { background: #fff3cd; color: #856404; }
  .score-red { background: #f8d7da; color: #721c24; }
  .score-dims { display: flex; flex-direction: column; gap: 5px; }
  .score-dim { display: flex; align-items: center; gap: 6px; font-size: 0.74rem; }
  .score-dim-icon { width: 16px; text-align: center; flex-shrink: 0; }
  .score-dim-label { width: 78px; color: #555; flex-shrink: 0; }
  .score-bar-bg { flex: 1; height: 6px; background: #e9ecef; border-radius: 3px; overflow: hidden; }
  .score-bar-fill { height: 100%; border-radius: 3px; }
  .score-dim-val { width: 26px; text-align: right; font-weight: 700; color: #333; flex-shrink: 0; }
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
    <a href="/" style="color:white;text-decoration:none;font-size:1.3rem;font-weight:800">City<span style="color:#a78bfa">Score</span></a>
    {% if communes|length == 1 %}<h1 style="font-size:1.1rem;font-weight:600">{{ communes[0] }}</h1>{% else %}<h1>Comparatif Communes</h1>{% endif %}
  </div>
  <p>DVF · RPLS · GéoRisques · Éducation · IPS · DNB · SNCF · OFGL · SIRENE · OSM · SSMSI · ARCEP · CAMS</p>
</header>
<div class="grid">
{% for c in communes %}
{% set d = data[c] %}
<div class="card">
  <div class="card-header">
    <h2>{{ c }}</h2>
    <div class="sub">INSEE {{ d.geo.code }} · CP {{ d.geo.codesPostaux[0] if d.geo.codesPostaux else '' }} · {{ d.geo.region.nom if d.geo.region else '' }}</div>
  </div>
  <div class="card-body">

  <!-- ══ CITYSCORE ══ -->
  {% if d.cityscore and d.cityscore.global is not none %}
  {% set gs = d.cityscore.global %}
  <div class="score-banner">
    <div class="score-global-row">
      <div class="score-circle {{ 'score-green' if gs >= 67 else ('score-orange' if gs >= 34 else 'score-red') }}">
        <span class="num">{{ gs }}</span><span class="lbl">score</span>
      </div>
      <div style="flex:1">
        <div style="font-size:0.72rem;font-weight:700;color:#333;margin-bottom:6px">CityScore</div>
        <div class="score-dims">
          {% for key, icon, label in [('immobilier','🏠','Immobilier'),('education','🎓','Éducation'),('securite','🛡','Sécurité'),('services','🏥','Services'),('cadre_vie','🌿','Cadre de vie')] %}
          {% set v = d.cityscore[key] %}
          {% if v is not none %}
          <div class="score-dim">
            <span class="score-dim-icon">{{ icon }}</span>
            <span class="score-dim-label">{{ label }}</span>
            <div class="score-bar-bg"><div class="score-bar-fill" style="width:{{ v }}%;background:{{ '#6bcb77' if v >= 67 else ('#ffd93d' if v >= 34 else '#ff6b6b') }}"></div></div>
            <span class="score-dim-val">{{ v }}</span>
          </div>
          {% endif %}
          {% endfor %}
        </div>
      </div>
    </div>
  </div>
  {% endif %}

  <!-- ══ 1. GÉOGRAPHIE & ACCÈS ══ -->
  <div class="cat">
    <div class="cat-header" onclick="toggle(this)">
      <span class="cat-icon">📍</span><span class="cat-title">Géographie & Accès</span><span class="cat-arrow">▼</span>
    </div>
    <div class="cat-body">
      <div class="kv"><span class="label">Population</span><span class="value">{{ "{:,}".format(d.geo.population or 0).replace(",", "\u202f") }} hab.</span></div>
      <div class="kv"><span class="label">Surface</span><span class="value">{{ d.geo.surface or '?' }} ha</span></div>
      <div class="kv"><span class="label">Lyon Part-Dieu (voiture)</span><span class="value">{{ d.distance.distance_km }} km · {{ d.distance.duree_min|int }} min</span></div>
      {% if d.fibre %}
      <div class="kv"><span class="label">Fibre optique (FTTH)</span><span class="value"><span class="badge {{ 'badge-green' if d.fibre.pct_thd1g >= 90 else ('badge-orange' if d.fibre.pct_thd1g >= 50 else 'badge-red') }}">{{ d.fibre.pct_thd1g }}%</span> <span style="font-size:0.72rem;color:#aaa">des logements — ARCEP {{ d.fibre.date[:4] if d.fibre.date else '' }}</span></span></div>
      {% endif %}
      {% set commercial_airports = [] %}
      {% for a in d.aeroports %}
        {% if 'Aéroport' in a.nom or 'aéroport' in a.nom or (a.iata and a.iata != '') %}
          {% set _ = commercial_airports.append(a) %}
        {% endif %}
      {% endfor %}
      {% if commercial_airports %}
      <div class="section" style="margin-top:10px">
        <div class="section-title">Aéroports commerciaux proches</div>
        {% for a in commercial_airports[:5] %}
        <div class="row2">
          <span style="font-size:0.8rem">{{ a.nom[:45] }}{% if a.iata %} <span class="badge badge-blue">{{ a.iata }}</span>{% endif %}</span>
          <span class="badge {{ 'badge-orange' if a.distance_km < 15 else ('badge-teal' if a.distance_km < 35 else 'badge-blue') }}">{{ a.distance_km }} km</span>
        </div>
        {% endfor %}
        {% set closest = commercial_airports[0] %}
        {% if closest.distance_km < 30 %}
        <div style="font-size:0.71rem;color:#e67e22;margin-top:4px;background:#fff3e0;padding:4px 6px;border-radius:4px">
          ⚠️ {{ closest.nom[:40] }} à {{ closest.distance_km }} km — <a href="https://www.geoportail-urbanisme.gouv.fr/" target="_blank" style="color:#e67e22">vérifier le PEB (Plan d'Exposition au Bruit)</a>
        </div>
        {% endif %}
      </div>
      {% endif %}
      <div class="section" style="margin-top:10px">
        <div class="section-title">Gares SNCF proches</div>
        {% for g in d.gares[:5] %}
        <div class="row2">
          <span>{{ g.nom or g.libellecourt or '?' }}</span>
          <span class="badge badge-blue">{{ g.distance_km if g.distance_km is defined else '?' }} km</span>
        </div>
        {% endfor %}
      </div>
    </div>
  </div>

  <!-- ══ 2. IMMOBILIER ══ -->
  <div class="cat">
    <div class="cat-header" onclick="toggle(this)">
      <span class="cat-icon">🏠</span><span class="cat-title">Immobilier (DVF 2022–2025)</span><span class="cat-arrow">▼</span>
    </div>
    <div class="cat-body">
      <div class="kv" style="margin-bottom:6px">
        <span class="label">Médian global</span>
        <span class="value"><strong>{{ d.dvf.global_median_m2|int }} €/m²</strong> · {{ d.dvf.total_habitation }} transactions</span>
      </div>
      <table class="price-table">
        <thead><tr><th>Année</th><th>Type</th><th>Nb</th><th>Médian/m²</th><th>Prix moy.</th></tr></thead>
        <tbody>
        {% for row in d.dvf.par_type %}
        <tr>
          <td>{{ row.annee }}</td>
          <td class="{{ 'maison' if row.type_local == 'Maison' else 'appt' }}">{{ row.type_local }}</td>
          <td>{{ row.nb|int }}</td>
          <td>{{ row.prix_m2_median|int }} €</td>
          <td>{{ (row.prix_moyen/1000)|round(0)|int }}k €</td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
      {% if d.logements_rp %}
      <div class="section" style="margin-top:10px">
        <div class="section-title">Parc de logements (INSEE RP 2022)</div>
        <div class="kv"><span class="label">Total logements</span><span class="value"><strong>{{ d.logements_rp.total }}</strong></span></div>
        <div class="kv"><span class="label">Résidences principales</span><span class="value">{{ d.logements_rp.residences_principales }} ({{ (d.logements_rp.residences_principales / d.logements_rp.total * 100)|round(0)|int }}%)</span></div>
        <div class="kv"><span class="label">Vacants / Secondaires</span><span class="value">{{ d.logements_rp.vacants }} / {{ d.logements_rp.residences_secondaires }}</span></div>
      </div>
      {% endif %}
      {% if d.hlm %}
      <div class="section" style="margin-top:10px">
        <div class="section-title">Logements sociaux RPLS 2021</div>
        <div class="kv"><span class="label">Parc social</span><span class="value">{{ d.hlm.total_2021 }} logements · {{ d.hlm.loyer_moyen_m2 }} €/m²{% if d.hlm_taux_pct %} · <strong>{{ d.hlm_taux_pct }}%</strong> du parc{% endif %}</span></div>
        <div class="kv"><span class="label">Occupés / Vacants</span><span class="value">{{ d.hlm.loges }} / {{ d.hlm.vacants }}</span></div>
      </div>
      {% endif %}
      {% if d.filosofi %}
      <div class="section" style="margin-top:10px">
        <div class="section-title">Revenus des ménages (FiLoSoFi 2021, INSEE)</div>
        <div class="kv"><span class="label">Revenu médian</span><span class="value"><strong>{{ "{:,.0f}".format(d.filosofi.revenu_median).replace(",", " ") }} €/an</strong></span></div>
        {% if d.filosofi.taux_pauvrete %}<div class="kv"><span class="label">Taux de pauvreté</span><span class="value"><span class="badge {{ 'badge-green' if d.filosofi.taux_pauvrete < 10 else ('badge-orange' if d.filosofi.taux_pauvrete < 20 else 'badge-red') }}">{{ d.filosofi.taux_pauvrete }}%</span></span></div>{% endif %}
        {% if d.dvf.global_median_m2 and d.filosofi.revenu_median %}<div class="kv"><span class="label">Ratio prix / revenu</span><span class="value">{{ ((d.dvf.global_median_m2 * 60) / d.filosofi.revenu_median)|round(1) }} ans de revenu pour 60 m²</span></div>{% endif %}
      </div>
      {% endif %}
    </div>
  </div>

  <!-- ══ 3. ÉDUCATION ══ -->
  <div class="cat">
    <div class="cat-header" onclick="toggle(this)">
      <span class="cat-icon">🏫</span><span class="cat-title">Éducation</span><span class="cat-arrow">▼</span>
    </div>
    <div class="cat-body">
      <div style="font-size:0.7rem;color:#aaa;margin-bottom:8px">IPS moy. nationale ≈ 100. VA bac = valeur ajoutée vs établissements similaires.</div>
      <div style="font-size:0.74rem;font-weight:700;color:#0f3460;margin-bottom:6px;padding:3px 8px;background:#e8f0fe;border-radius:4px">
        📍 Dans {{ d.commune_name }} ({{ d.schools_count_in }})
      </div>
      {% for s in d.schools_in %}
      <div class="school-item">
        <div class="school-name">{{ s.nom }}</div>
        <div class="school-meta">
          <span class="badge {{ 'badge-blue' if s.public else 'badge-purple' }}">{{ 'Public' if s.public else 'Privé' }}</span>
          <span>{{ s.nature }}</span>
          {% if s.ips %}<span class="badge {{ 'badge-green' if s.ips >= 110 else ('badge-orange' if s.ips >= 90 else 'badge-red') }}">IPS {{ s.ips }}</span><span class="ips-ref">/ {{ s.ips_national }} nat.</span>{% endif %}
          {% if s.dnb %}<span class="badge badge-blue">Brevet {{ s.dnb.annee }} : {{ s.dnb.taux }}</span>{% endif %}
        </div>
        {% if s.ips %}<div class="ips-bar-wrap"><div class="ips-bar-bg"><div class="ips-bar" style="width:{{ [s.ips/1.5,100]|min }}%"></div></div></div>{% endif %}
      </div>
      {% endfor %}
      {% if d.schools_out %}
      <div style="font-size:0.74rem;font-weight:700;color:#666;margin:10px 0 6px;padding:3px 8px;background:#f8f9fa;border-radius:4px">
        🔀 Communes voisines ({{ d.schools_count_out }})
      </div>
      {% for s in d.schools_out %}
      <div class="school-item">
        <div class="school-name">{{ s.nom }} <span style="color:#aaa;font-size:0.71rem">— {{ s.commune }}</span></div>
        <div class="school-meta">
          <span class="badge {{ 'badge-blue' if s.public else 'badge-purple' }}">{{ 'Public' if s.public else 'Privé' }}</span>
          <span>{{ s.nature }}</span>
          {% if s.ips %}<span class="badge {{ 'badge-green' if s.ips >= 110 else ('badge-orange' if s.ips >= 90 else 'badge-red') }}">IPS {{ s.ips }}</span>{% endif %}
          {% if s.dnb %}<span class="badge badge-blue">Brevet {{ s.dnb.annee }} : {{ s.dnb.taux }}</span>{% endif %}
        </div>
      </div>
      {% endfor %}
      {% endif %}
      {% if d.lycees %}
      <div style="font-size:0.74rem;font-weight:700;color:#333;margin:10px 0 6px">🎓 Lycées proches</div>
      {% for l in d.lycees %}
      <div class="school-item">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span style="font-size:0.81rem;font-weight:500">{{ l.nom }}</span>
          <span class="badge badge-blue">{{ l.dist }} km</span>
        </div>
        <div class="school-meta">
          <span>{{ l.cp }}</span>
          <span class="badge {{ 'badge-blue' if l.public else 'badge-purple' }}">{{ 'Public' if l.public else 'Privé' }}</span>
          {% if l.bac %}<span class="badge {{ 'badge-green' if l.bac.taux >= 90 else 'badge-orange' }}">Bac {{ l.bac.annee }} : {{ l.bac.taux }}%</span>{% if l.bac.mention %}<span class="badge badge-purple">Mention {{ l.bac.mention }}%</span>{% endif %}{% if l.bac.va is not none %}<span class="badge {{ 'badge-green' if l.bac.va > 0 else 'badge-red' }}">VA {{ '+' if l.bac.va > 0 else '' }}{{ l.bac.va }}</span>{% endif %}{% endif %}
        </div>
      </div>
      {% endfor %}
      {% endif %}
    </div>
  </div>

  <!-- ══ 4. SANTÉ & SERVICES ══ -->
  <div class="cat">
    <div class="cat-header" onclick="toggle(this)">
      <span class="cat-icon">🏥</span><span class="cat-title">Santé & Services de proximité</span><span class="cat-arrow">▼</span>
    </div>
    <div class="cat-body">
      {% if d.medecins %}
      <div class="section-title">Professionnels de santé (SIRENE)</div>
      <div class="chips">
        {% for label, nb in d.medecins.par_type.items() %}
        <div class="chip"><span>{{ label }}</span><span class="num">{{ nb }}</span></div>
        {% endfor %}
      </div>
      {% if d.medecins.generalistes_noms %}
      <div style="font-size:0.72rem;color:#666;margin-top:6px">Généralistes : {{ d.medecins.generalistes_noms[:5] | join(', ') }}{% if d.medecins.generalistes_noms|length > 5 %} <em>+{{ d.medecins.generalistes_noms|length - 5 }} autres</em>{% endif %}</div>
      {% endif %}
      {% endif %}
      {% if d.bpe %}
      <div class="section-title" style="margin-top:10px">Services essentiels (BPE INSEE 2024)</div>
      <div style="font-size:0.72rem;color:#aaa;margin-bottom:6px">{{ d.bpe.nb_present }}/{{ d.bpe.nb_total }} types de services présents dans la commune</div>
      <div class="chips">
        {% for svc in d.bpe.services %}
        <div class="chip"><span>{{ svc.label }}</span><span class="num">{{ svc.count }}</span></div>
        {% endfor %}
      </div>
      {% endif %}
      {% if d.commerces %}
      <div class="section-title" style="margin-top:10px">Commerces & équipements (OpenStreetMap, ~4km)</div>
      <div class="chips">
        {% for label, nb in d.commerces.par_type.items() %}
        <div class="chip"><span>{{ label }}</span><span class="num">{{ nb }}</span></div>
        {% endfor %}
      </div>
      {% endif %}
    </div>
  </div>

  <!-- ══ 5. BUDGET & FISCALITÉ ══ -->
  <div class="cat">
    <div class="cat-header" onclick="toggle(this)">
      <span class="cat-icon">💰</span><span class="cat-title">Budget & Fiscalité</span><span class="cat-arrow">▼</span>
    </div>
    <div class="cat-body">
      {% if d.budget_recettes %}
      <div class="section-title">Recettes de fonctionnement</div>
      {% for b in d.budget_recettes[:3] %}
      <div class="kv"><span class="label">{{ b.exer }}</span><span class="value">{{ "{:,.0f}".format(b.euros_par_habitant or 0) }} €/hab · {{ "{:,.0f}".format((b.montant or 0)/1000) }}k €</span></div>
      {% endfor %}
      {% endif %}
      {% if d.budget_depenses %}
      <div class="section-title" style="margin-top:8px">Dépenses de fonctionnement</div>
      {% for b in d.budget_depenses[:3] %}
      <div class="kv"><span class="label">{{ b.exer }}</span><span class="value">{{ "{:,.0f}".format(b.euros_par_habitant or 0) }} €/hab · {{ "{:,.0f}".format((b.montant or 0)/1000) }}k €</span></div>
      {% endfor %}
      {% endif %}
      {% if d.impots_locaux %}
      <div class="section-title" style="margin-top:8px">Impôts locaux collectés par la commune</div>
      {% set latest = d.impots_locaux[0] %}
      <div class="kv">
        <span class="label">{{ latest.exer }} — Total collecté</span>
        <span class="value"><strong>{{ "{:,.0f}".format(latest.euros_par_habitant or 0) }} €/hab</strong> · {{ "{:,.0f}".format((latest.montant or 0)/1000) }}k €</span>
      </div>
      {% for b in d.impots_locaux[1:3] %}
      <div class="kv"><span class="label">{{ b.exer }}</span><span class="value">{{ "{:,.0f}".format(b.euros_par_habitant or 0) }} €/hab</span></div>
      {% endfor %}
      <div style="background:#fffbf0;border:1px solid #ffe58f;border-radius:6px;padding:8px 10px;margin-top:8px;font-size:0.75rem;color:#7d6608">
        <strong>Taxe foncière estimée (propriétaire)</strong><br>
        {% set tf_foyer = (latest.euros_par_habitant or 0) * 0.45 * 2.3 %}
        Pour un foyer de 2–3 personnes : environ <strong>{{ tf_foyer|int }}–{{ (tf_foyer * 1.3)|int }} €/an</strong><br>
        <span style="color:#aaa;font-size:0.7rem">Estimation (TFB ≈ 40–50% des impôts locaux, ménage ~2.3 pers.) — taux exact en mairie</span>
      </div>
      {% endif %}
      <!-- Taxe d'aménagement piscine -->
      <div class="section-title" style="margin-top:12px">Taxe d'aménagement — Piscine</div>
      <div style="font-size:0.72rem;color:#666;margin-bottom:6px">Formule : surface bassin (m²) × 250 €/m² × (taux communal + taux départ.) — base nationale 2024</div>
      {% set ta_base = 25 * 250 %}
      {% set ta_rate_dep = 0.025 %}
      {% set ta_rate_com_min = 0.03 %}
      {% set ta_rate_com_max = 0.05 %}
      <div class="kv"><span class="label">Exemple — piscine 25 m²</span><span class="value"><strong>{{ (ta_base * (ta_rate_com_min + ta_rate_dep))|int }}–{{ (ta_base * (ta_rate_com_max + ta_rate_dep))|int }} €</strong> (one-shot)</span></div>
      <div class="kv"><span class="label">Base de calcul</span><span class="value">{{ ta_base }} € · Taux dept. Rhône ~2,5%</span></div>
      <div style="font-size:0.7rem;color:#aaa;margin-top:4px">Taux communal 3–5% (fourchette habituelle) · À vérifier auprès de la mairie</div>
    </div>
  </div>

  <!-- ══ 6. SÉCURITÉ ══ -->
  <div class="cat">
    <div class="cat-header" onclick="toggle(this)">
      <span class="cat-icon">🔒</span><span class="cat-title">Sécurité</span><span class="cat-arrow">▼</span>
    </div>
    <div class="cat-body">
      {% if d.criminalite %}
      <div style="font-size:0.7rem;color:#aaa;margin-bottom:6px">Taux pour 1 000 habitants — source SSMSI / Ministère de l'Intérieur</div>
      {% for item in d.criminalite %}
      <div class="kv">
        <span class="label" style="font-size:0.78rem">{{ item.indicateur }}</span>
        <span class="value">
          <span class="badge {{ 'badge-green' if item.taux_pour_mille < 2 else ('badge-orange' if item.taux_pour_mille < 6 else 'badge-red') }}">{{ "%.1f"|format(item.taux_pour_mille) }}/1000</span>
          <span style="font-size:0.68rem;color:#bbb;margin-left:3px">{{ item.annee }}</span>
        </span>
      </div>
      {% endfor %}
      {% else %}
      <div class="kv"><span class="label" style="color:#aaa">Données non diffusées</span></div>
      {% endif %}
    </div>
  </div>

  <!-- ══ 7. ENVIRONNEMENT & RISQUES ══ -->
  <div class="cat">
    <div class="cat-header" onclick="toggle(this)">
      <span class="cat-icon">🌿</span><span class="cat-title">Environnement & Risques</span><span class="cat-arrow">▼</span>
    </div>
    <div class="cat-body">
      {% if d.qualite_air %}
      <div class="section-title">Qualité de l'air (Open-Meteo / CAMS)</div>
      <div class="kv">
        <span class="label">Indice EAQI</span>
        <span class="value">
          <span class="badge {{ 'badge-green' if d.qualite_air.eaqi_color == 'green' else ('badge-orange' if d.qualite_air.eaqi_color == 'orange' else 'badge-red') }}">{{ d.qualite_air.eaqi_label }} ({{ d.qualite_air.eaqi }})</span>
        </span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 8px">
        <span class="chip"><span>PM10</span><span class="num">{{ d.qualite_air.pm10 }} µg/m³</span></span>
        <span class="chip"><span>PM2.5</span><span class="num">{{ d.qualite_air.pm2_5 }} µg/m³</span></span>
        <span class="chip"><span>NO₂</span><span class="num">{{ d.qualite_air.nitrogen_dioxide }} µg/m³</span></span>
        <span class="chip"><span>O₃</span><span class="num">{{ d.qualite_air.ozone }} µg/m³</span></span>
      </div>
      {% endif %}
      <div class="section-title">Risques naturels & industriels (GéoRisques)</div>
      <div class="risk-list">
      {% for r in d.risques %}
      <span class="risk-tag {{ 'industrial' if 'industriel' in r.lower() or 'thermique' in r.lower() or 'toxique' in r.lower() else '' }}">{{ r[:45] }}{{ '…' if r|length > 45 else '' }}</span>
      {% endfor %}
      </div>
      {% if d.dechetteries %}
      <div class="section-title" style="margin-top:10px">Déchetteries les plus proches (OSM)</div>
      {% for dech in d.dechetteries %}
      <div class="row2">
        <span>{{ dech.nom }}</span>
        <span class="badge badge-teal">{{ dech.distance_km }} km</span>
      </div>
      {% endfor %}
      {% endif %}
    </div>
  </div>

  </div><!-- card-body -->
</div><!-- card -->
{% endfor %}
</div>
<script>
function toggle(header) {
  header.classList.toggle('open');
  header.nextElementSibling.classList.toggle('open');
}
</script>
<footer>Données publiques françaises · DVF · RPLS · GéoRisques · Éducation · OFGL · SIRENE · OSM · SSMSI · {{ now }}</footer>
</body>
</html>"""


def load_data(commune: str) -> dict:
    base = DATA_DIR / commune
    out = {}

    def load_json(key):
        p = base / f"{key}.json"
        return json.load(open(p)) if p.exists() else None

    # Geo
    out["geo"] = load_json("geo") or {}

    # Distance Lyon
    out["distance"] = load_json("distance_lyon") or {}

    # DVF
    out["dvf"] = load_json("dvf_summary") or {}

    # HLM
    out["hlm"] = load_json("hlm_rpls")

    # Gares avec distance
    gares_raw = (load_json("gares_sncf") or {}).get("results", [])
    out["gares"] = sorted(gares_raw, key=lambda g: g.get("distance_km", 999))

    # Build IPS lookup by UAI
    ips_map = {}
    for ds in ["ips_ecoles", "ips_colleges"]:
        data = load_json(ds)
        if data:
            for rec in data.get("results", []):
                uai = rec.get("uai")
                if uai:
                    year = rec.get("rentree_scolaire", "")
                    # Keep the most recent year
                    if uai not in ips_map or year > ips_map[uai].get("annee", ""):
                        ips_map[uai] = {
                            "ips": rec.get("ips"),
                            "ips_national": rec.get("ips_national"),
                            "annee": year,
                        }

    # Build DNB lookup by UAI
    dnb_map = {}
    dnb_raw = load_json("dnb_resultats")
    if dnb_raw:
        for rec in dnb_raw.get("results", []):
            uai = rec.get("uai") or rec.get("numero_d_etablissement")
            year = str(rec.get("session", ""))
            if uai and (uai not in dnb_map or year > dnb_map[uai].get("annee", "")):
                dnb_map[uai] = {
                    "annee": year,
                    "taux": rec.get("taux_de_reussite", "?"),
                    "admis": rec.get("admis"),
                    "mentions_bien": rec.get("admis_mention_bien"),
                    "mentions_tb": rec.get("admis_mention_tres_bien"),
                }

    # Schools enriched — grouped by in-city vs neighboring communes
    geo = out.get("geo", {})
    commune_insee = geo.get("code", "")
    commune_name = geo.get("nom", "")

    schools_raw = load_json("schools") or {}
    schools_in = []
    schools_out = []

    for s in schools_raw.get("results", []):
        uai = s.get("identifiant_de_l_etablissement")
        ips = ips_map.get(uai, {})
        dnb = dnb_map.get(uai)
        nature = s.get("libelle_nature") or s.get("type_etablissement", "")
        if any(kw in nature.upper() for kw in ["ADMINISTRATIF", "ADMINISTRATION", "INSPECTION", "SERVICE"]):
            continue
        entry = {
            "nom": s.get("nom_etablissement", "?"),
            "public": s.get("statut_public_prive") == "Public",
            "nature": nature,
            "commune": s.get("nom_commune", ""),
            "uai": uai,
            "ips": float(ips["ips"]) if ips.get("ips") else None,
            "ips_national": float(ips["ips_national"]) if ips.get("ips_national") else None,
            "dnb": dnb,
        }
        if s.get("code_commune") == commune_insee:
            schools_in.append(entry)
        else:
            schools_out.append(entry)

    def school_sort_key(s):
        n = s["nature"]
        return (0 if "Lycée" in n else 1 if "Collège" in n else 2 if "Primaire" in n or "lément" in n else 3)

    out["schools_in"] = sorted(schools_in, key=school_sort_key)
    out["schools_out"] = sorted(schools_out, key=school_sort_key)
    out["schools_count_in"] = len(schools_in)
    out["schools_count_out"] = len(schools_out)
    out["commune_name"] = commune_name

    # Lycées proches
    lycees_raw = (load_json("lycees_proches") or {}).get("results", [])
    lycees = []
    for l in lycees_raw:
        bac = l.get("bac")
        lycees.append({
            "nom": l.get("nom_etablissement", "?"),
            "cp": l.get("code_postal", ""),
            "public": l.get("statut_public_prive") == "Public",
            "dist": l.get("distance_km", "?"),
            "bac": {
                "annee": bac.get("annee", "?"),
                "taux": bac.get("taux_reussite"),
                "mention": bac.get("taux_mention"),
                "va": bac.get("valeur_ajoutee"),
            } if bac else None,
        })
    out["lycees"] = sorted(lycees, key=lambda l: l.get("dist", 999) if isinstance(l.get("dist"), (int, float)) else 999)[:6]

    # Budget
    for key in ["budget_recettes", "budget_depenses", "impots_locaux"]:
        data = load_json(key)
        out[key] = data.get("results", []) if data else []

    # Risques
    risques = []
    risk_data = load_json("risques")
    if risk_data:
        for item in risk_data.get("data", []):
            risques += [d["libelle_risque_long"] for d in item.get("risques_detail", [])]
    out["risques"] = risques

    # Criminalité
    crime_data = load_json("criminalite")
    out["criminalite"] = [
        i for i in (crime_data or {}).get("indicateurs", [])
        if i.get("taux_pour_mille") is not None
    ]

    # Déchetteries
    dech_data = load_json("dechetterie")
    out["dechetteries"] = (dech_data or {}).get("results", [])

    # Médecins & professionnels de santé
    out["medecins"] = load_json("medecins")

    # Commerces & équipements OSM
    out["commerces"] = load_json("commerces_osm")

    # Aéroports proches
    out["aeroports"] = (load_json("aeroports") or {}).get("results", [])

    # Qualité de l'air
    out["qualite_air"] = load_json("qualite_air")

    # Fibre optique (ARCEP)
    out["fibre"] = load_json("fibre")

    # BPE équipements du quotidien
    out["bpe"] = load_json("bpe")

    # FiLoSoFi — revenus et pauvreté (INSEE 2021)
    out["filosofi"] = load_json("filosofi")

    # Logements RP (Melodi INSEE census 2022)
    out["logements_rp"] = load_json("logements_rp")

    # Taux HLM = logements sociaux / total logements RP (INSEE census — most accurate)
    hlm = out.get("hlm")
    lrp = out.get("logements_rp")
    if hlm and lrp and lrp.get("total"):
        out["hlm_taux_pct"] = round(hlm.get("total_2021", 0) / lrp["total"] * 100, 1)
    else:
        out["hlm_taux_pct"] = None

    out["cityscore"] = compute_cityscore(out)
    return out


def compute_cityscore(d: dict) -> dict:
    scores: dict[str, int | None] = {}

    # ── Immobilier : évolution (35%) + prix absolu (25%) + HLM (15%) + tension (15%) + vacance (10%) ──
    dvf = d.get("dvf") or {}
    par_type = dvf.get("par_type") or []

    # Weighted average price per year across all transaction types
    year_data: dict[int, list] = {}
    for row in par_type:
        y, p, n = row.get("annee"), row.get("prix_m2_median"), row.get("nb", 0)
        if y and p and n:
            year_data.setdefault(y, []).append((p, n))

    def _wavg(items):
        tw = sum(n for _, n in items)
        return sum(p * n for p, n in items) / tw if tw else None

    yrs = sorted(year_data)
    evol_score = None
    if len(yrs) >= 2:
        p_old = _wavg(year_data[yrs[0]])
        p_new = _wavg(year_data[yrs[-1]])
        if p_old and p_new:
            ep = (p_new - p_old) / p_old * 100  # evolution %
            if 5 <= ep <= 20:
                evol_score = 100
            elif ep > 20:
                evol_score = max(40, 100 - (ep - 20) * 3)   # penalise excessive rise
            elif ep >= 0:
                evol_score = 60 + ep / 5 * 40               # stable → 60-100
            else:
                evol_score = max(0, 60 + ep * 4)            # decline → 0-60

    median_m2 = dvf.get("global_median_m2")
    revenu_median = (d.get("filosofi") or {}).get("revenu_median")
    prix_score = None
    if median_m2:
        if revenu_median:
            # Ratio prix/revenu : nb d'années de revenu médian pour acheter 60m²
            # 3 ans = 100pts (très accessible), 8 ans = 50pts, 15+ ans = 0pts
            ratio = (median_m2 * 60) / revenu_median
            prix_score = max(0, min(100, (15 - ratio) / 12 * 100))
        else:
            # Fallback sur prix absolu si FiLoSoFi non disponible
            if median_m2 < 1500:
                prix_score = 20
            elif median_m2 <= 3000:
                prix_score = 60 + (median_m2 - 1500) / 1500 * 40
            elif median_m2 <= 5000:
                prix_score = 100
            elif median_m2 <= 7000:
                prix_score = max(40, 100 - (median_m2 - 5000) / 2000 * 60)
            else:
                prix_score = 20

    # HLM: 0% = 100pts, ≥40% = 0pts (adouci vs v1 qui pénalisait à 30%)
    hlm_taux = d.get("hlm_taux_pct")
    hlm_score = max(0, min(100, (40 - hlm_taux) / 40 * 100)) if hlm_taux is not None else None

    # Tension marché: transactions habitation / 1000 logements / an
    logements = (d.get("logements_rp") or {}).get("residences_principales") or 0
    total_hab_txn = sum(r.get("nb", 0) for r in par_type)
    nb_years = max(len(yrs), 1)
    tension_score = None
    if logements and total_hab_txn:
        txn_per_1000 = (total_hab_txn / nb_years) / logements * 1000
        # <10/1000/an = 0pts, 20 = 50pts, ≥40 = 100pts
        tension_score = max(0, min(100, (txn_per_1000 - 10) / 30 * 100))

    # Vacance: logements vacants / total
    logements_data = d.get("logements_rp") or {}
    vacants = logements_data.get("vacants")
    total_log = logements_data.get("total")
    vacance_score = None
    if vacants is not None and total_log:
        taux_vacance = vacants / total_log * 100
        # <5% = 100pts, ≥12% = 0pts (signe de déclin)
        vacance_score = max(0, min(100, (12 - taux_vacance) / 7 * 100))

    sub_immo = [(s, w) for s, w in [
        (evol_score, 0.35), (prix_score, 0.25), (hlm_score, 0.15),
        (tension_score, 0.15), (vacance_score, 0.10)
    ] if s is not None]
    if sub_immo:
        tw = sum(w for _, w in sub_immo)
        scores["immobilier"] = round(sum(s * w for s, w in sub_immo) / tw)
    else:
        scores["immobilier"] = None

    # ── Éducation : IPS (40%) + taux DNB (25%) + mentions (25%) + densité (10%) ─
    schools_in  = d.get("schools_in") or []
    schools_out = d.get("schools_out") or []

    def _parse_pct(v):
        try:
            return float(str(v).replace("%", "").replace(",", "."))
        except Exception:
            return None

    # Use in-commune schools for quality metrics; fall back to all if none have data
    def _dnb_schools(pool):
        return [s for s in pool if s.get("dnb") and s["dnb"]]

    quality_pool = _dnb_schools(schools_in) or _dnb_schools(schools_in + schools_out)
    ips_pool = [s for s in (schools_in or (schools_in + schools_out)) if s.get("ips")]

    ips_vals = [s["ips"] for s in ips_pool]
    # IPS centré sur 100 (moyenne nationale publique) : 100=50pts, 155=100pts, 60=0pts
    ips_sc = max(0, min(100, 50 + (sum(ips_vals) / len(ips_vals) - 100) / 55 * 50)) if ips_vals else None

    dnb_vals = [v for v in [_parse_pct((s["dnb"] or {}).get("taux")) for s in quality_pool] if v is not None]
    # taux DNB: 50%=0pts, 100%=100pts
    dnb_sc = max(0, min(100, (sum(dnb_vals) / len(dnb_vals) - 50) / 50 * 100)) if dnb_vals else None

    # Taux mentions bien + très bien sur les écoles IN seulement
    mention_rates = []
    for s in quality_pool:
        dnb = s["dnb"] or {}
        admis = dnb.get("admis") or 0
        bien = (dnb.get("mentions_bien") or 0) + (dnb.get("mentions_tb") or 0)
        if admis > 0:
            mention_rates.append(bien / admis * 100)
    # 20% mentions = 0pts, 60% = 100pts
    mention_sc = max(0, min(100, (sum(mention_rates) / len(mention_rates) - 20) / 40 * 100)) if mention_rates else None

    # Densité scolaire : nb écoles dans la commune / 1000 habitants
    pop = (d.get("geo") or {}).get("population") or 1
    density = len(schools_in) / pop * 1000
    # 0.3/1000 = 0pts, 1.5/1000 = 100pts
    density_sc = max(0, min(100, (density - 0.3) / 1.2 * 100))

    sub_edu = [(s, w) for s, w in [(ips_sc, 0.4), (dnb_sc, 0.25), (mention_sc, 0.25), (density_sc, 0.1)] if s is not None]
    if sub_edu:
        tw = sum(w for _, w in sub_edu)
        scores["education"] = round(sum(s * w for s, w in sub_edu) / tw)
    else:
        scores["education"] = None

    # ── Sécurité : z-score par catégorie vs stats nationales ────────────────
    crimes = d.get("criminalite") or []
    _crime_stats_path = DATA_DIR / "_national" / "crime_stats.json"
    _national_crime = None
    if _crime_stats_path.exists():
        try:
            with open(_crime_stats_path) as _f:
                _national_crime = json.load(_f)
        except Exception:
            pass

    # Severity weights: violence ×3, burglary ×2, theft/other ×1
    def _crime_severity(indicateur: str) -> int:
        low = indicateur.lower()
        # Violent crime: starts with "violence" or contains "coups" or "sexuel"
        # Exclude "sans violence" (which is theft, not violence)
        if "sans violence" in low:
            return 1
        if low.startswith("violence") or "coups" in low or "sexuel" in low:
            return 3
        if "cambriolage" in low:
            return 2
        return 1

    if crimes and _national_crime:
        weighted_sum = 0.0
        weighted_w = 0.0
        for c in crimes:
            taux = c.get("taux_pour_mille")
            if taux is None:
                continue
            indic = c.get("indicateur", "")
            stats = _national_crime.get(indic)
            if not stats:
                continue
            # Use min IQR of 2.0 to avoid extreme sensitivity for near-zero categories
            iqr = max(stats["iqr"], 2.0)
            z = (taux - stats["median"]) / iqr
            # z=0 (at median) → 75pts; z=1 → 50pts; z=-1 → 100pts; z=3 → 0pts
            cat_score = max(0.0, min(100.0, 100 - (z + 1) * 25))
            w = _crime_severity(indic)
            weighted_sum += cat_score * w
            weighted_w += w
        scores["securite"] = round(weighted_sum / weighted_w) if weighted_w else None
    elif crimes:
        # Fallback: simple cumulative rate (0‰=100, 100‰=0)
        total_taux = sum(c.get("taux_pour_mille", 0) for c in crimes)
        scores["securite"] = round(max(0, min(100, (100 - total_taux) / 100 * 100)))
    else:
        scores["securite"] = None

    # ── Services : médecins/hab (35%) + gare (20%) + BPE équipements (45%) ──
    pop = (d.get("geo") or {}).get("population") or 1
    med_par_type = (d.get("medecins") or {}).get("par_type") or {}
    gen = med_par_type.get("Médecin généraliste", 0)
    # 2 généralistes / 1000 hab = 100pts
    gen_sc = min(100, gen / pop * 1000 / 2 * 100)
    gares = d.get("gares") or []
    gare_dist = gares[0].get("distance_km", 50) if gares else 50
    gare_sc = max(0, min(100, (30 - gare_dist) / 30 * 100))
    # BPE: % services essentiels présents (boulangerie, supermarché, pharmacie, poste, cinéma...)
    bpe_sc = (d.get("bpe") or {}).get("score_presence")
    sub_svc = [(s, w) for s, w in [(gen_sc, 0.35), (gare_sc, 0.20), (bpe_sc, 0.45)] if s is not None]
    if sub_svc:
        tw = sum(w for _, w in sub_svc)
        scores["services"] = round(sum(s * w for s, w in sub_svc) / tw)
    else:
        scores["services"] = None

    # ── Cadre de vie : air + fibre + risques pondérés ───────────────────────
    sub = []
    eaqi = (d.get("qualite_air") or {}).get("eaqi")
    if eaqi:
        sub.append(max(0, min(100, (6 - eaqi) / 5 * 100)))
    sub.append((d.get("fibre") or {}).get("pct_thd1g") or 0)
    # Risques pondérés par gravité: inondation/Seveso ×3, séisme ×2, autres ×1
    _RISQUE_POIDS = {3: ["inondation", "seveso", "industriel", "nucléaire", "crue"],
                     2: ["séisme", "mouvement de terrain", "glissement"]}
    risques = d.get("risques") or []
    risque_weighted = 0.0
    for r in risques:
        rl = r.lower()
        w = next((p for p, kws in _RISQUE_POIDS.items() if any(k in rl for k in kws)), 1)
        risque_weighted += w
    # 0 poids = 100pts; 30 poids équivalent (≈10 risques graves) = 0pts
    sub.append(max(0, min(100, (30 - risque_weighted) / 30 * 100)))
    scores["cadre_vie"] = round(sum(sub) / len(sub)) if sub else None

    # ── Score global pondéré ─────────────────────────────────────────────────
    weights = {"immobilier": 0.25, "education": 0.25, "securite": 0.20,
               "services": 0.15, "cadre_vie": 0.15}
    valid = [(k, v) for k, v in scores.items() if v is not None]
    if valid:
        tw = sum(weights[k] for k, _ in valid)
        scores["global"] = round(sum(weights[k] * v for k, v in valid) / tw)
    else:
        scores["global"] = None

    return scores


@app.route("/")
def index():
    # All collected communes from data dir, sorted by most recently collected
    all_communes = []
    if DATA_DIR.exists():
        for d in sorted(DATA_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if d.is_dir() and (d / "geo.json").exists():
                all_communes.append(d.name)

    slugs = {c: to_slug(c) for c in all_communes}
    pops, dists = {}, {}
    for c in all_communes:
        geo = json.loads((DATA_DIR / c / "geo.json").read_text()) if (DATA_DIR / c / "geo.json").exists() else {}
        dist = json.loads((DATA_DIR / c / "distance_lyon.json").read_text()) if (DATA_DIR / c / "distance_lyon.json").exists() else {}
        pops[c] = f"{geo.get('population', 0):,}".replace(",", "\u202f")
        dists[c] = dist.get("distance_km", "?")
    return render_template_string(TEMPLATE_HOME, communes=all_communes, slugs=slugs, pops=pops, dists=dists)


@app.route("/commune/<slug>")
def commune_page(slug: str):
    from flask import request as _req
    force_refresh = _req.args.get("refresh") == "1"
    commune_name = find_commune_dir(slug)

    if commune_name and data_is_fresh(commune_name) and not force_refresh:
        data = {commune_name: load_data(commune_name)}
        return render_template_string(TEMPLATE, communes=[commune_name], data=data, now=datetime.now().strftime("%d/%m/%Y %H:%M"))

    # Force refresh: delete metadata so start_collection triggers re-collect
    if force_refresh and commune_name:
        meta = DATA_DIR / commune_name / "metadata.json"
        if meta.exists():
            meta.unlink()
        with _jobs_lock:
            _jobs.pop(slug, None)

    # Data missing or stale — need collection
    if not commune_name:
        commune_name, commune_info = lookup_commune_api(slug)
        if not commune_name:
            return f"<h2>Commune introuvable : {slug}</h2><p><a href='/'>← Retour</a></p>", 404
    else:
        # Build info from existing geo.json
        geo_path = DATA_DIR / commune_name / "geo.json"
        if geo_path.exists():
            geo = json.loads(geo_path.read_text())
            coords = geo.get("centre", {}).get("coordinates", [0, 0])
            commune_info = {
                "code": geo.get("code", ""),
                "cp": (geo.get("codesPostaux") or [""])[0],
                "dept": geo.get("departement", {}).get("code", ""),
                "lat": coords[1], "lon": coords[0],
            }
        else:
            _, commune_info = lookup_commune_api(slug)

    with _jobs_lock:
        status = _jobs.get(slug)

    if status == "done":
        # Collection just finished, serve data
        data = {commune_name: load_data(commune_name)}
        return render_template_string(TEMPLATE, communes=[commune_name], data=data, now=datetime.now().strftime("%d/%m/%Y %H:%M"))

    if status != "collecting":
        start_collection(slug, commune_name, commune_info)

    return render_template_string(TEMPLATE_LOADING, commune_name=commune_name, slug=slug)


@app.route("/compare")
def compare():
    data = {c: load_data(c) for c in COMMUNES}
    return render_template_string(TEMPLATE, communes=COMMUNES, data=data, now=datetime.now().strftime("%d/%m/%Y %H:%M"))


@app.route("/api/search")
def api_search():
    from flask import request
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    try:
        r = req.get(
            "https://geo.api.gouv.fr/communes",
            params={"nom": q, "fields": "nom,code,codesPostaux,departement", "boost": "population", "limit": 8},
            timeout=5,
        )
        results = []
        for item in (r.json() if r.ok else []):
            results.append({
                "nom": item["nom"],
                "slug": to_slug(item["nom"]),
                "code": item["code"],
                "dept": item.get("departement", {}).get("nom", ""),
            })
        return jsonify(results)
    except Exception:
        return jsonify([])


@app.route("/api/status/<slug>")
def api_status(slug: str):
    with _jobs_lock:
        status = _jobs.get(slug, "unknown")
    return jsonify({"status": status})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5055, debug=False)
