"""
Collecte de données publiques par commune (région lyonnaise).

Sources confirmées :
  - geo.api.gouv.fr               → infos géo de base
  - data.education.gouv.fr        → établissements scolaires, IPS, DNB
  - georisques.gouv.fr            → risques naturels
  - data.ofgl.fr                  → finances locales (budget, taxes, dépenses)
  - files.data.gouv.fr            → DVF (transactions immobilières)
  - ressources.data.sncf.com      → gares voyageurs proches + distance
  - router.project-osrm.org       → distance routière vers Lyon
  - tabular-api.data.gouv.fr      → logements sociaux RPLS 2021
  - recherche-entreprises.api.gouv.fr → établissements employeurs (SIRENE)

Usage : python collect.py
"""
import io
import json
import math
import time
import warnings
from pathlib import Path

import pandas as pd
import requests

warnings.filterwarnings("ignore")  # SSL warnings

OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)

COMMUNES = {
    "Saint-Pierre-de-Chandieu": {
        "code": "69289", "cp": "69780", "dept": "69",
        "lat": 45.6471, "lon": 5.0085,
    },
    "Chaponnay": {
        "code": "69270", "cp": "69970", "dept": "69",
        "lat": 45.6315, "lon": 4.9584,
    },
    "Saint-Laurent-de-Mure": {
        "code": "69288", "cp": "69720", "dept": "69",
        "lat": 45.6842, "lon": 5.0597,
    },
}

# Lyon Part-Dieu (référence pour les distances)
LYON_LAT, LYON_LON = 45.7602, 4.8267

# RPLS 2021 — resource CSV sur tabular-api.data.gouv.fr
RPLS_RESOURCE_ID = "e94f91e3-d50b-4281-abb3-8ec7725dc656"

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 CityDataCollector/1.0"


def save(name: str, key: str, data):
    path = OUT / name / f"{key}.json"
    path.parent.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ {key}")


def get(url, params=None, verify=True, timeout=12) -> dict | None:
    try:
        r = S.get(url, params=params, verify=verify, timeout=timeout)
        if r.ok:
            return r.json()
        print(f"  ✗ {url.split('//')[-1][:60]}: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ✗ {url.split('//')[-1][:60]}: {e.__class__.__name__}")
    return None


# ── Collecteurs ───────────────────────────────────────────────────────────────

def fetch_geo(name: str, info: dict):
    """Infos géographiques de base (population, surface, région, dép.)."""
    data = get(
        f"https://geo.api.gouv.fr/communes/{info['code']}",
        params={"fields": "nom,code,codesPostaux,departement,region,population,surface,centre"},
    )
    if data:
        save(name, "geo", data)


def fetch_schools(name: str, info: dict):
    """Établissements scolaires dans la commune."""
    data = get(
        "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/fr-en-annuaire-education/records",
        params={
            "where": f"code_postal='{info['cp']}'",
            "select": "identifiant_de_l_etablissement,nom_etablissement,type_etablissement,"
                      "statut_public_prive,libelle_nature,adresse_1,latitude,longitude,"
                      "nom_commune,code_commune,"
                      "restauration,hebergement,ulis,voie_generale,voie_technologique,"
                      "voie_professionnelle,etat",
            "limit": 50,
        },
    )
    if data:
        save(name, "schools", data)


def fetch_risks(name: str, info: dict):
    """Risques naturels et technologiques (GéoRisques GASPAR)."""
    data = get(
        "https://georisques.gouv.fr/api/v1/gaspar/risques",
        params={"code_insee": info["code"]},
        verify=False,
    )
    if data:
        save(name, "risques", data)


def fetch_dvf(name: str, info: dict):
    """Prix de vente immobiliers (DVF) — 3 dernières années."""
    results = []
    for year in [2022, 2023, 2024, 2025]:
        url = f"https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/communes/{info['dept']}/{info['code']}.csv"
        try:
            r = S.get(url, timeout=15)
            if r.ok:
                df = pd.read_csv(io.StringIO(r.text), low_memory=False)
                df["annee"] = year
                results.append(df)
            else:
                print(f"  ✗ DVF {year}: HTTP {r.status_code}")
        except Exception as e:
            print(f"  ✗ DVF {year}: {e.__class__.__name__}")

    if results:
        df_all = pd.concat(results, ignore_index=True)
        df_ventes = df_all[df_all["nature_mutation"] == "Vente"].copy()
        df_ventes["valeur_fonciere"] = pd.to_numeric(
            df_ventes["valeur_fonciere"].astype(str).str.replace(",", "."), errors="coerce"
        )
        df_ventes["surface_reelle_bati"] = pd.to_numeric(df_ventes["surface_reelle_bati"], errors="coerce")

        df_habitation = df_ventes[df_ventes["type_local"].isin(["Maison", "Appartement"])].copy()
        df_habitation = df_habitation[
            df_habitation["surface_reelle_bati"].notna() &
            (df_habitation["surface_reelle_bati"] > 10) &
            df_habitation["valeur_fonciere"].notna() &
            (df_habitation["valeur_fonciere"] > 10_000)
        ]
        df_habitation["prix_m2"] = df_habitation["valeur_fonciere"] / df_habitation["surface_reelle_bati"]

        # Filtrer les ventes groupées (même date + même valeur foncière = immeuble vendu en bloc)
        # → conserver une seule ligne par (date, valeur) et recalculer le prix/m² sur la surface totale
        ventes_groupees = df_habitation.groupby(["date_mutation", "valeur_fonciere"]).filter(lambda g: len(g) > 1)
        if not ventes_groupees.empty:
            idx_garder = df_habitation.groupby(["date_mutation", "valeur_fonciere"]).apply(
                lambda g: pd.Series({"idx": g.index.tolist(), "surface_tot": g["surface_reelle_bati"].sum()})
            )
            to_drop = []
            for _, row in idx_garder.iterrows():
                idxs = row["idx"]
                surf_tot = row["surface_tot"]
                if len(idxs) > 1:
                    # Garder seulement le premier, corriger son prix/m²
                    to_drop.extend(idxs[1:])
                    df_habitation.loc[idxs[0], "surface_reelle_bati"] = surf_tot
                    val = df_habitation.loc[idxs[0], "valeur_fonciere"]
                    df_habitation.loc[idxs[0], "prix_m2"] = val / surf_tot
            df_habitation = df_habitation.drop(index=to_drop)

        summary = {
            "total_mutations": len(df_ventes),
            "total_habitation": len(df_habitation),
            "par_type": df_habitation.groupby(["annee", "type_local"]).agg(
                nb=("prix_m2", "count"),
                prix_m2_median=("prix_m2", "median"),
                prix_m2_moyen=("prix_m2", "mean"),
                prix_moyen=("valeur_fonciere", "mean"),
                surface_moyenne=("surface_reelle_bati", "mean"),
            ).round(0).reset_index().to_dict(orient="records"),
            "global_median_m2": round(df_habitation["prix_m2"].median(), 0),
            "global_mean_m2":   round(df_habitation["prix_m2"].mean(), 0),
        }
        save(name, "dvf_summary", summary)

        df_habitation[["date_mutation", "annee", "type_local", "valeur_fonciere",
                        "surface_reelle_bati", "prix_m2", "nombre_pieces_principales"]].to_csv(
            OUT / name / "dvf_raw.csv", index=False
        )
        print(f"  ✓ dvf_raw.csv ({len(df_habitation)} transactions)")


def fetch_budget(name: str, info: dict):
    """Budget et fiscalité (OFGL — données communales)."""
    base_url = "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/ofgl-base-communes-consolidee/records"
    for label, agregat, key in [
        ("recettes", "Recettes de fonctionnement", "budget_recettes"),
        ("depenses", "Dépenses de fonctionnement", "budget_depenses"),
        ("taxes", "Impôts locaux", "impots_locaux"),
        ("investissement", "Dépenses d'investissement", "budget_investissement"),
    ]:
        data = get(base_url, params={
            "where": f"insee='{info['code']}' AND agregat='{agregat}'",
            "select": "exer,montant,euros_par_habitant",
            "order_by": "exer desc",
            "limit": 5,
        })
        if data:
            save(name, key, data)


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return round(R * 2 * math.asin(math.sqrt(a)), 1)


def fetch_nearby_communes(name: str, info: dict):
    """Communes voisines dans un rayon de 10km."""
    data = get(
        "https://geo.api.gouv.fr/communes",
        params={
            "lat": info["lat"],
            "lon": info["lon"],
            "fields": "nom,code,population,codesPostaux",
            "type": "commune-actuelle",
            "limit": 20,
        },
    )
    if data:
        save(name, "communes_proches", data)


def fetch_transport(name: str, info: dict):
    """Gares SNCF dans un rayon de 20km + distance calculée + distance routière vers Lyon."""
    data = get(
        "https://ressources.data.sncf.com/api/explore/v2.1/catalog/datasets/gares-de-voyageurs/records",
        params={
            "where": f"distance(position_geographique, geom'POINT({info['lon']} {info['lat']})', 20km)",
            "select": "nom,libellecourt,codeinsee,position_geographique",
            "order_by": f"distance(position_geographique, geom'POINT({info['lon']} {info['lat']})')",
            "limit": 10,
        },
    )
    if data:
        # Enrichir avec la distance calculée
        for rec in data.get("results", []):
            pos = rec.get("position_geographique")
            if pos and isinstance(pos, dict):
                lat2 = pos.get("lat") or pos.get("latitude")
                lon2 = pos.get("lon") or pos.get("longitude")
                if lat2 and lon2:
                    rec["distance_km"] = _haversine_km(info["lat"], info["lon"], float(lat2), float(lon2))
        save(name, "gares_sncf", data)

    # Distance routière vers Lyon Part-Dieu via OSRM (pas de clé API requise)
    osrm = get(
        f"http://router.project-osrm.org/route/v1/driving/{info['lon']},{info['lat']};{LYON_LON},{LYON_LAT}",
        params={"overview": "false"},
        timeout=10,
    )
    if osrm and osrm.get("routes"):
        route = osrm["routes"][0]
        save(name, "distance_lyon", {
            "distance_km": round(route["distance"] / 1000, 1),
            "duree_min": round(route["duration"] / 60, 0),
            "destination": "Lyon Part-Dieu",
        })


def fetch_hlm(name: str, info: dict):
    """Logements sociaux RPLS 2021 (loyers, vacance, DPE, taille)."""
    data = get(
        f"https://tabular-api.data.gouv.fr/api/resources/{RPLS_RESOURCE_ID}/data/",
        params={"COM__exact": info["code"], "page_size": 1},
    )
    if data and data.get("data"):
        rec = data["data"][0]
        # Calcul % logements sociaux vs total (approximatif : LOUE+VACANT / population * 2.4 pers/foyer)
        total_hlm = rec.get("TOT21", 0) or 0
        save(name, "hlm_rpls", {
            "commune": rec.get("NCOM"),
            "loges": rec.get("LOUE", 0),
            "vacants": rec.get("VACANT", 0),
            "total_2021": total_hlm,
            "evolution": {
                "2021": rec.get("TOT21", 0), "2020": rec.get("TOT20", 0),
                "2019": rec.get("TOT19", 0), "2018": rec.get("TOT18", 0),
                "2017": rec.get("TOT17", 0),
            },
            "repartition_tailles": {
                "T1": rec.get("1P", 0), "T2": rec.get("2P", 0),
                "T3": rec.get("3P", 0), "T4": rec.get("4P", 0),
                "T5+": rec.get("5P", 0),
            },
            "dpe": {
                "A": rec.get("DPE-A", 0), "B": rec.get("DPE-B", 0),
                "C": rec.get("DPE-C", 0), "D": rec.get("DPE-D", 0),
                "E": rec.get("DPE-E", 0), "F": rec.get("DPE-F", 0),
                "G": rec.get("DPE-G", 0), "NR": rec.get("DPE-NR", 0),
            },
            "age_moyen_ans": rec.get("AGE-MOY"),
            "loyer_moyen_m2": round(rec.get("LOYERMOY", 0), 2),
            "epci": rec.get("NEPCI"),
        })


def fetch_school_ratings(name: str, info: dict):
    """IPS (Indice de Position Sociale) pour écoles et collèges."""
    # IPS écoles primaires
    data = get(
        "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/fr-en-ips-ecoles-ap2022/records",
        params={
            "where": f"code_insee_de_la_commune='{info['code']}'",
            "order_by": "rentree_scolaire desc",
            "limit": 20,
        },
    )
    if data:
        save(name, "ips_ecoles", data)

    # IPS collèges
    data2 = get(
        "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/fr-en-ips-colleges-ap2023/records",
        params={
            "where": f"code_insee_de_la_commune='{info['code']}'",
            "order_by": "rentree_scolaire desc",
            "limit": 10,
        },
    )
    if data2:
        save(name, "ips_colleges", data2)


def fetch_dnb_results(name: str, info: dict):
    """Résultats au Diplôme National du Brevet (DNB) pour les collèges de la commune."""
    # Récupérer les UAI des collèges depuis l'annuaire
    schools_path = OUT / name / "schools.json"
    if not schools_path.exists():
        return
    schools = json.load(open(schools_path)).get("results", [])
    college_uais = [
        s["identifiant_de_l_etablissement"] for s in schools
        if s.get("type_etablissement") == "Collège" and s.get("identifiant_de_l_etablissement")
    ]
    if not college_uais:
        return

    all_results = []
    for uai in college_uais:
        data = get(
            "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/fr-en-dnb-par-etablissement/records",
            params={
                "where": f"numero_d_etablissement='{uai}'",
                "order_by": "session desc",
                "limit": 5,
            },
        )
        if data and data.get("results"):
            for rec in data["results"]:
                rec["uai"] = uai
            all_results.extend(data["results"])

    if all_results:
        save(name, "dnb_resultats", {"results": all_results, "total_count": len(all_results)})


def fetch_lycees_nearby(name: str, info: dict):
    """Lycées dans un rayon de 15km avec résultats au bac."""
    # Lycées proches via l'annuaire
    data = get(
        "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/fr-en-annuaire-education/records",
        params={
            "where": f"distance(position, geom'POINT({info['lon']} {info['lat']})', 15km) AND type_etablissement='Lycée'",
            "select": "identifiant_de_l_etablissement,nom_etablissement,statut_public_prive,"
                      "code_postal,commune,latitude,longitude",
            "limit": 10,
        },
    )
    if not data or not data.get("results"):
        return

    lycees = data["results"]
    # Ajouter distance et résultats bac
    for lycee in lycees:
        lat2 = lycee.get("latitude")
        lon2 = lycee.get("longitude")
        if lat2 and lon2:
            lycee["distance_km"] = _haversine_km(info["lat"], info["lon"], float(lat2), float(lon2))

        uai = lycee.get("identifiant_de_l_etablissement")
        if uai:
            bac = get(
                "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/fr-en-indicateurs-de-resultat-des-lycees-gt_v2/records",
                params={"where": f"uai='{uai}'", "order_by": "annee desc", "limit": 1},
            )
            if bac and bac.get("results"):
                r = bac["results"][0]
                lycee["bac"] = {
                    "annee": r.get("annee"),
                    "taux_reussite": r.get("taux_reu_total"),
                    "taux_mention": r.get("taux_men_total"),
                    "valeur_ajoutee": r.get("va_reu_total"),
                }
        time.sleep(0.1)

    save(name, "lycees_proches", {"results": lycees, "total_count": len(lycees)})


def fetch_sirene(name: str, info: dict):
    """Établissements actifs (SIRENE) — secteurs d'activité dominants."""
    data = get(
        "https://recherche-entreprises.api.gouv.fr/search",
        params={
            "code_postal": info["cp"],
            "per_page": 25,
        },
    )
    if data:
        save(name, "sirene_etablissements", data)


# NAF codes santé → libellé affiché
_NAF_SANTE = {
    "86.21Z": "Médecin généraliste",
    "86.22Z": "Médecin spécialiste",
    "86.23Z": "Chirurgien-dentiste",
    "86.90D": "Infirmier",
    "86.90E": "Kinésithérapeute / rééducation",
    "86.10Z": "Hôpital / clinique",
    "86.90F": "Laboratoire / technicien médical",
    "86.90B": "Orthophoniste / orthoptiste",
}


def fetch_medecins(name: str, info: dict):
    """Professionnels de santé libéraux par commune (SIRENE section Q)."""
    data = get(
        "https://recherche-entreprises.api.gouv.fr/search",
        params={
            "code_commune": info["code"],
            "section_activite_principale": "Q",
            "per_page": 25,
        },
    )
    if not data:
        return
    counts: dict[str, int] = {}
    noms_generalistes: list[str] = []
    for e in data.get("results", []):
        naf = e.get("activite_principale", "")
        label = _NAF_SANTE.get(naf)
        if label:
            counts[label] = counts.get(label, 0) + 1
            if naf == "86.21Z":
                nom = e.get("nom_complet") or e.get("nom_raison_sociale", "")
                if nom:
                    noms_generalistes.append(nom.title())
    save(name, "medecins", {
        "par_type": counts,
        "total": sum(counts.values()),
        "generalistes_noms": noms_generalistes,
        "source": "SIRENE / recherche-entreprises.api.gouv.fr",
    })


def fetch_commerces(name: str, info: dict):
    """Commerces et services de proximité via OpenStreetMap (bbox)."""
    delta = 0.04  # ~4km
    bbox = f"{info['lat']-delta},{info['lon']-delta},{info['lat']+delta},{info['lon']+delta}"
    query = (
        f"[out:json][timeout:25];"
        f"("
        f"node[\"amenity\"~\"pharmacy|doctors|hospital|restaurant|cafe|fast_food|bank|post_office\"]({bbox});"
        f"node[\"shop\"~\"supermarket|convenience|bakery|butcher|greengrocer\"]({bbox});"
        f"node[\"leisure\"~\"sports_centre|fitness_centre|swimming_pool\"]({bbox});"
        f");out tags;"
    )
    try:
        r = S.post("https://overpass-api.de/api/interpreter", data={"data": query}, timeout=35)
        if not r.ok:
            print(f"  ✗ Overpass commerces: HTTP {r.status_code}")
            return
        elements = r.json().get("elements", [])
    except Exception as e:
        print(f"  ✗ Overpass commerces: {e.__class__.__name__}")
        return

    _LABELS = {
        "pharmacy": "Pharmacie", "doctors": "Cabinet médical",
        "hospital": "Hôpital", "restaurant": "Restaurant", "cafe": "Café",
        "fast_food": "Restauration rapide", "bank": "Banque", "post_office": "La Poste",
        "supermarket": "Supermarché", "convenience": "Épicerie",
        "bakery": "Boulangerie", "butcher": "Boucherie", "greengrocer": "Primeur",
        "sports_centre": "Centre sportif", "fitness_centre": "Salle de sport",
        "swimming_pool": "Piscine",
    }
    counts: dict[str, int] = {}
    for e in elements:
        tags = e.get("tags", {})
        for field in ("amenity", "shop", "leisure"):
            val = tags.get(field, "")
            label = _LABELS.get(val)
            if label:
                counts[label] = counts.get(label, 0) + 1
                break

    save(name, "commerces_osm", {
        "par_type": counts,
        "total": sum(counts.values()),
        "source": "OpenStreetMap",
    })


CRIME_CSV_URL = (
    "https://static.data.gouv.fr/resources/bases-statistiques-communale-departementale"
    "-et-regionale-de-la-delinquance-enregistree-par-la-police-et-la-gendarmerie"
    "-nationales/20260326-124144/donnee-data.gouv-2025-geographie2025-produit-le2026-02-03.csv.gz"
)
_crime_cache: dict | None = None  # cache in-memory pour éviter 2x download


def _load_crime_csv() -> dict:
    """Télécharge et indexe le CSV national de délinquance (une seule fois par run)."""
    global _crime_cache
    if _crime_cache is not None:
        return _crime_cache
    import gzip
    print("  ↓ Téléchargement base nationale criminalité (~40 Mo)...")
    try:
        r = S.get(CRIME_CSV_URL, timeout=90, stream=False)
        if not r.ok:
            print(f"  ✗ Criminalité CSV: HTTP {r.status_code}")
            _crime_cache = {}
            return _crime_cache
    except Exception as e:
        print(f"  ✗ Criminalité CSV: {e.__class__.__name__}")
        _crime_cache = {}
        return _crime_cache

    import csv, io
    index: dict = {}
    with gzip.open(io.BytesIO(r.content), "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            code = row.get("CODGEO_2025", "")
            if not code:
                continue
            if code not in index:
                index[code] = {}
            indic = row.get("indicateur", "")
            annee = row.get("annee", "")
            if row.get("est_diffuse") != "diff":
                continue
            if indic not in index[code] or annee > index[code][indic]["annee"]:
                taux_str = row.get("taux_pour_mille", "").replace(",", ".")
                try:
                    taux = float(taux_str) if taux_str and taux_str not in ("NA", "") else None
                except ValueError:
                    taux = None
                index[code][indic] = {
                    "annee": annee,
                    "nombre": row.get("nombre"),
                    "taux_pour_mille": taux,
                }
    _crime_cache = index
    print(f"  ✓ Base criminalité chargée ({len(index)} communes)")
    return _crime_cache


def fetch_criminalite(name: str, info: dict):
    """Statistiques de délinquance communale (SSMSI / Ministère de l'Intérieur)."""
    index = _load_crime_csv()
    commune_data = index.get(info["code"], {})
    if not commune_data:
        print(f"  ✗ Criminalité: commune {info['code']} non trouvée dans la base")
        return

    # Garder les indicateurs avec taux > 0, triés par taux décroissant
    indicateurs = [
        {"indicateur": indic, **vals}
        for indic, vals in commune_data.items()
        if vals.get("taux_pour_mille") is not None
    ]
    indicateurs.sort(key=lambda x: x.get("taux_pour_mille") or 0, reverse=True)
    save(name, "criminalite", {"indicateurs": indicateurs, "total_categories": len(indicateurs)})


def fetch_dechetterie(name: str, info: dict):
    """Déchetteries proches via OpenStreetMap (Overpass API, bbox)."""
    # Bounding box ~25km autour de la commune (bbox: S,W,N,E)
    delta = 0.22
    bbox = f"{info['lat']-delta},{info['lon']-delta},{info['lat']+delta},{info['lon']+delta}"
    query = (
        f"[out:json][timeout:25];"
        f"(node[\"recycling_type\"=\"centre\"]({bbox});"
        f"way[\"recycling_type\"=\"centre\"]({bbox});"
        f");out center tags;"
    )
    try:
        r = S.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=35,
        )
        if not r.ok:
            print(f"  ✗ Overpass API: HTTP {r.status_code}")
            return
        elements = r.json().get("elements", [])
    except Exception as e:
        print(f"  ✗ Overpass API: {e.__class__.__name__}")
        return

    seen = set()
    results = []
    for e in elements:
        tags = e.get("tags", {})
        nom = tags.get("name") or tags.get("operator") or "Déchetterie"
        # Ignorer les PAV (points d'apport volontaire) sans nom de déchetterie
        if nom and not any(k in nom.lower() for k in ["déchett", "dechett"]):
            if tags.get("recycling:glass") and not tags.get("recycling_type") == "centre":
                continue
        lat = e.get("lat") or e.get("center", {}).get("lat")
        lon = e.get("lon") or e.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        key = (round(float(lat), 4), round(float(lon), 4))
        if key in seen:
            continue
        seen.add(key)
        dist = _haversine_km(info["lat"], info["lon"], float(lat), float(lon))
        results.append({
            "nom": nom,
            "lat": lat,
            "lon": lon,
            "distance_km": round(dist, 1),
            "tags": {k: v for k, v in tags.items() if k in (
                "addr:city", "addr:street", "opening_hours", "phone", "website", "operator"
            )},
        })

    results.sort(key=lambda x: x["distance_km"])
    save(name, "dechetterie", {"results": results[:5], "total_found": len(results)})


_EAQI_LABELS = [
    (20, "Bon", "green"), (40, "Moyen", "orange"),
    (60, "Dégradé", "orange"), (80, "Mauvais", "red"),
    (100, "Très mauvais", "red"),
]


def fetch_aeroports(name: str, info: dict):
    """Aérodromes les plus proches via Nominatim (OpenStreetMap)."""
    results = []
    seen = set()
    for query_term in ("aerodrome", "airport"):
        # ±0.5° lat (~55km), ±0.7° lon (~50km at 45°N)
        vb = f"{info['lon']-0.7},{info['lat']-0.5},{info['lon']+0.7},{info['lat']+0.5}"
        r = get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": query_term,
                "format": "json", "limit": 20,
                "viewbox": vb,
                "bounded": 1, "extratags": 1,
            },
        )
        if not r:
            continue
        for item in r:
            nom = item.get("display_name", "").split(",")[0].strip()
            if nom in seen:
                continue
            seen.add(nom)
            alat = float(item.get("lat", 0))
            alon = float(item.get("lon", 0))
            dist = _haversine_km(info["lat"], info["lon"], alat, alon)
            if dist > 80:
                continue
            extras = item.get("extratags") or {}
            results.append({
                "nom": nom,
                "iata": extras.get("iata", ""),
                "icao": extras.get("icao", ""),
                "type": extras.get("aerodrome:type", "civil"),
                "distance_km": round(dist, 1),
                "lat": alat,
                "lon": alon,
            })
        time.sleep(1)  # Nominatim rate limit: 1 req/s

    results.sort(key=lambda x: x["distance_km"])
    save(name, "aeroports", {"results": results[:8], "total_found": len(results)})


def fetch_logements_rp(name: str, info: dict):
    """Parc de logements total par statut d'occupation (Melodi RP 2022)."""
    geo_vintage = "2025"  # millésime géographique
    params = {
        "GEO": f"{geo_vintage}-COM-{info['code']}",
        "RP_MEASURE": "DWELLINGS",
        "NRG_SRC": "_T", "CARS": "_T", "NOR": "_T", "BUILD_END": "_T",
        "TDW": "_T", "TSH": "_T", "CARPARK": "_T", "L_STAY": "_T",
        "TIME_PERIOD": "2022",
        "maxResult": 20,
    }
    data = get("https://api.insee.fr/melodi/data/DS_RP_LOGEMENT_PRINC", params=params)
    if not data:
        return
    totals = {}
    for obs in data.get("observations", []):
        ocs = obs.get("dimensions", {}).get("OCS")
        val = obs.get("measures", {}).get("OBS_VALUE_NIVEAU", {}).get("value")
        if ocs and val is not None:
            totals[ocs] = round(val)
    if totals:
        save(name, "logements_rp", {
            "total": totals.get("_T"),
            "residences_principales": totals.get("DW_MAIN"),
            "vacants": totals.get("DW_VAC"),
            "residences_secondaires": totals.get("DW_SEC_DW_OCC"),
            "annee": "2022",
            "source": "INSEE Melodi RP",
        })


def fetch_qualite_air(name: str, info: dict):
    """Qualité de l'air en temps réel via Open-Meteo (CAMS / Copernicus)."""
    data = get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        params={
            "latitude": info["lat"],
            "longitude": info["lon"],
            "current": "european_aqi,pm10,pm2_5,nitrogen_dioxide,ozone",
            "timezone": "Europe/Paris",
        },
    )
    if not data or "current" not in data:
        return
    c = data["current"]
    eaqi = c.get("european_aqi")
    label, color = "Extrêmement mauvais", "red"
    for threshold, lbl, col in _EAQI_LABELS:
        if eaqi is not None and eaqi <= threshold:
            label, color = lbl, col
            break
    save(name, "qualite_air", {
        "eaqi": eaqi,
        "eaqi_label": label,
        "eaqi_color": color,
        "pm10": c.get("pm10"),
        "pm2_5": c.get("pm2_5"),
        "nitrogen_dioxide": c.get("nitrogen_dioxide"),
        "ozone": c.get("ozone"),
        "updated_at": c.get("time"),
        "source": "Open-Meteo / CAMS Copernicus",
    })


_arcep_cache: dict | None = None


def _load_arcep_csv() -> dict:
    """Télécharge et indexe le CSV ARCEP fiber par commune INSEE (une seule fois)."""
    global _arcep_cache
    if _arcep_cache is not None:
        return _arcep_cache
    print("  ↓ Téléchargement stats fibre ARCEP par commune (~1 Mo)...")
    try:
        r = S.get(
            "https://data.arcep.fr/fixe/maconnexioninternet/statistiques/last/commune/commune_debit_filaire.csv",
            allow_redirects=True, timeout=30,
        )
        if not r.ok:
            print(f"  ✗ ARCEP fibre CSV: HTTP {r.status_code}")
            _arcep_cache = {}
            return _arcep_cache
    except Exception as e:
        print(f"  ✗ ARCEP fibre CSV: {e.__class__.__name__}")
        _arcep_cache = {}
        return _arcep_cache

    import csv, io
    index = {}
    reader = csv.DictReader(io.StringIO(r.text), delimiter=";")
    for row in reader:
        code = row.get("code_insee", "").strip()
        if code:
            index[code] = row
    _arcep_cache = index
    print(f"  ✓ ARCEP fibre chargé ({len(index)} communes)")
    return _arcep_cache


def fetch_fibre(name: str, info: dict):
    """Taux d'éligibilité fibre optique (ARCEP Ma connexion internet 2025T4)."""
    index = _load_arcep_csv()
    row = index.get(info["code"])
    if not row:
        print(f"  ✗ Fibre: commune {info['code']} non trouvée")
        return
    total = int(row.get("nbr") or 0)
    thd100 = int(row.get("elig_thd100") or 0)
    thd1g = int(row.get("elig_thd1g") or 0)
    save(name, "fibre", {
        "total_logements": total,
        "elig_thd100": thd100,
        "elig_thd1g": thd1g,
        "pct_thd100": round(thd100 / total * 100, 1) if total else None,
        "pct_thd1g": round(thd1g / total * 100, 1) if total else None,
        "date": row.get("date"),
        "source": "ARCEP Ma connexion internet",
    })


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(name: str):
    """Affiche un résumé rapide des données collectées."""
    base = OUT / name
    files = list(base.glob("*.json")) + list(base.glob("*.csv"))
    print(f"\n  --- Résumé {name} ---")

    geo_path = base / "geo.json"
    if geo_path.exists():
        g = json.load(open(geo_path))
        print(f"  Population : {g.get('population', '?'):,}  |  Surface : {g.get('surface', '?')} ha")

    dist_path = base / "distance_lyon.json"
    if dist_path.exists():
        d = json.load(open(dist_path))
        print(f"  Distance Lyon : {d['distance_km']} km | {d['duree_min']:.0f} min en voiture")

    dvf_path = base / "dvf_summary.json"
    if dvf_path.exists():
        d = json.load(open(dvf_path))
        print(f"  Prix médian m² : {d.get('global_median_m2', '?')} €  ({d.get('total_habitation','?')} transactions 2022-2024)")
        for rec in d.get("par_type", []):
            print(f"    {rec['annee']} {rec['type_local']:12} : {rec['nb']:2.0f} ventes | "
                  f"médian {rec['prix_m2_median']:.0f} €/m² | prix moy {rec['prix_moyen']:.0f} €")

    hlm_path = base / "hlm_rpls.json"
    if hlm_path.exists():
        h = json.load(open(hlm_path))
        print(f"  HLM (RPLS 2021) : {h['total_2021']} logements sociaux | loyer moy {h['loyer_moyen_m2']} €/m²")

    risk_path = base / "risques.json"
    if risk_path.exists():
        r = json.load(open(risk_path))
        risques = []
        for item in r.get("data", []):
            risques += [d["libelle_risque_long"] for d in item.get("risques_detail", [])]
        print(f"  Risques ({len(risques)}) : {', '.join(risques[:3])}{'...' if len(risques) > 3 else ''}")

    schools_path = base / "schools.json"
    if schools_path.exists():
        s = json.load(open(schools_path))
        print(f"  Établissements scolaires : {s.get('total_count', 0)}")

    gares_path = base / "gares_sncf.json"
    if gares_path.exists():
        g = json.load(open(gares_path))
        gares = [r.get("nom", r.get("libellecourt", "?")) for r in g.get("results", [])[:3]]
        print(f"  Gares proches : {', '.join(gares)}")

    print(f"  Fichiers collectés : {len(files)}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    for name, info in COMMUNES.items():
        print(f"\n{'='*55}")
        print(f"  {name} (INSEE: {info['code']}, CP: {info['cp']})")
        print(f"{'='*55}")
        fetch_geo(name, info)
        fetch_schools(name, info)
        fetch_risks(name, info)
        fetch_dvf(name, info)
        fetch_budget(name, info)
        fetch_nearby_communes(name, info)
        fetch_transport(name, info)
        fetch_hlm(name, info)
        fetch_school_ratings(name, info)
        fetch_dnb_results(name, info)
        fetch_lycees_nearby(name, info)
        fetch_sirene(name, info)
        fetch_medecins(name, info)
        fetch_commerces(name, info)
        fetch_criminalite(name, info)
        fetch_dechetterie(name, info)
        fetch_aeroports(name, info)
        fetch_logements_rp(name, info)
        fetch_qualite_air(name, info)
        fetch_fibre(name, info)
        time.sleep(0.5)

    print("\n" + "="*55)
    for name in COMMUNES:
        print_summary(name)

    print("\nDone. Données dans ./data/")


def collect_commune(name: str, info: dict):
    """Collect all data for a single commune. Called on-demand from app.py."""
    fetch_geo(name, info)
    fetch_schools(name, info)
    fetch_risks(name, info)
    fetch_dvf(name, info)
    fetch_budget(name, info)
    fetch_nearby_communes(name, info)
    fetch_transport(name, info)
    fetch_hlm(name, info)
    fetch_school_ratings(name, info)
    fetch_dnb_results(name, info)
    fetch_lycees_nearby(name, info)
    fetch_sirene(name, info)
    fetch_medecins(name, info)
    fetch_commerces(name, info)
    fetch_criminalite(name, info)
    fetch_dechetterie(name, info)
    fetch_aeroports(name, info)
    fetch_logements_rp(name, info)
    fetch_qualite_air(name, info)
    fetch_fibre(name, info)


if __name__ == "__main__":
    main()
