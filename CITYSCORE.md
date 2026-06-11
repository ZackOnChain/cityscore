# CityScore — Documentation du projet

## Vision

CityScore aide les particuliers à **trouver leur futur lieu de vie en France** en agrégeant des données publiques gratuites, centralisées et lisibles. Pour chaque commune française, une fiche détaillée est générée à la demande, couvrant l'immobilier, l'éducation, la sécurité, les services et le cadre de vie.

## Architecture

```
collect.py          → collecte les données publiques par commune (sources ci-dessous)
app.py              → serveur Flask : routing, scoring, rendu HTML
data/<Commune>/     → fichiers JSON générés par commune (ignorés dans le repo)
```

**Stack** : Python · Flask · Jinja2 · Cloudflare Tunnel  
**URL** : `/commune/<slug>` — collecte automatique à la demande, cache 7 jours  
**Repo** : https://github.com/ZackOnChain/cityscore

---

## Sources de données

| Catégorie | Source | Endpoint |
|-----------|--------|----------|
| Géographie & population | geo.api.gouv.fr | `/communes/{code}` |
| Transactions immobilières (DVF) | files.data.gouv.fr | DVF par département |
| Logements sociaux (RPLS 2021) | tabular-api.data.gouv.fr | resource RPLS |
| Établissements scolaires | data.education.gouv.fr | annuaire-education |
| IPS écoles & collèges | data.education.gouv.fr | depp-ips-* |
| Résultats DNB | data.education.gouv.fr | dnb-par-etablissement |
| Risques naturels & industriels | georisques.gouv.fr | `/gaspar/api/` |
| Budget communal | data.ofgl.fr | ofgl-base-com |
| Taxe foncière & d'aménagement | data.ofgl.fr | impots-locaux |
| Gares SNCF proches | ressources.data.sncf.com | referentiel-gares |
| Distance routière vers Lyon | router.project-osrm.org | route/v1/driving |
| Logements RP 2022 (INSEE) | api.insee.fr/melodi | DS_RP_LOGEMENT_PRINC |
| Criminalité | files.data.gouv.fr | ssmsi-stat-insécurité |
| Médecins & santé (SIRENE) | recherche-entreprises.api.gouv.fr | section Q, paginé |
| Qualité de l'air (EAQI) | open-meteo.com | air-quality-api |
| Fibre optique (ARCEP) | data.arcep.fr | commune_debit_filaire |
| Aéroports proches | nominatim.openstreetmap.org | search |

---

## CityScore — Calcul détaillé (v1)

Le CityScore est un **score global de 0 à 100** calculé à partir de 5 dimensions. Chaque dimension est notée de 0 à 100 puis pondérée.

### Pondération globale

| Dimension | Poids |
|-----------|-------|
| 🏠 Immobilier | 25 % |
| 🎓 Éducation | 25 % |
| 🛡 Sécurité | 20 % |
| 🏥 Services | 15 % |
| 🌿 Cadre de vie | 15 % |

---

### 🏠 Immobilier

Composé de 3 sous-indicateurs :

| Sous-indicateur | Poids | Formule |
|----------------|-------|---------|
| Évolution des prix | 50 % | +5% à +20% sur 3 ans = 100pts · stable = 60pts · déclin penalisé · >30% penalisé (inabordable) |
| Prix absolu | 20 % | <1500€/m² = 20pts · 1500-5000€ zone optimale → 100pts · >7000€ = 20pts |
| Taux HLM | 30 % | <5% HLM = 100pts · >30% HLM = 0pts (linéaire) |

**Source prix** : DVF (mutations 2022-2025), médiane pondérée par type (maison/appt) et nombre de ventes  
**Source HLM** : RPLS 2021 / total logements RP INSEE 2022

**Limites connues** :
- L'évolution des prix peut être bruitée sur les petites communes (<20 transactions/an)
- Le taux HLM est calculé sur le parc 2021 × recensement 2022 (léger décalage temporel)
- La notion "prix idéal" est discutable : un prix très bas peut signifier un marché peu dynamique ou une commune en déclin

---

### 🎓 Éducation

Composé de 4 sous-indicateurs, calculés **en priorité sur les écoles de la commune** (fallback sur les écoles proches si aucune donnée locale disponible) :

| Sous-indicateur | Poids | Formule |
|----------------|-------|---------|
| IPS moyen | 40 % | Centré sur 100 (moyenne nationale) : 100 = 50pts · 155 = 100pts · 60 = 0pts |
| Taux de réussite DNB | 25 % | 50% = 0pts · 100% = 100pts (linéaire) |
| Taux mentions bien + TB | 25 % | 20% = 0pts · 60% = 100pts (linéaire) |
| Densité scolaire | 10 % | Nb écoles in-commune / 1000 hab · 0.3 = 0pts · 1.5 = 100pts |

**Sources** : data.education.gouv.fr (annuaire, IPS collèges/écoles, DNB par établissement)

**Limites connues** :
- L'IPS mesure le profil socio-économique des élèves, **pas directement la qualité pédagogique**
- La valeur ajoutée (ce qu'apporte vraiment l'école) n'est pas disponible via API publique dans les sources actuelles
- Le DNB concerne uniquement les collèges — les communes sans collège se basent sur l'IPS seul
- Les écoles privées sous contrat ont des IPS structurellement plus élevés que les publiques

---

### 🛡 Sécurité

| Sous-indicateur | Poids | Formule |
|----------------|-------|---------|
| Taux criminalité cumulé | 100 % | Somme des taux_pour_mille de toutes les catégories · 0‰ = 100pts · ≥100‰ = 0pts |

**Source** : SSMSI (Service Statistique Ministériel de la Sécurité Intérieure), base nationale par commune

**Limites connues** :
- Sommer des taux de différentes catégories (cambriolages, violences, vols…) n'est pas statistiquement rigoureux — ils se cumulent artificiellement pour les grandes villes
- À retravailler en v2 : normaliser par catégorie vs moyenne nationale, puis agréger
- Les communes rurales avec très peu de faits déclarés peuvent avoir 0 donnée (score nul par défaut)

---

### 🏥 Services

| Sous-indicateur | Poids | Formule |
|----------------|-------|---------|
| Médecins généralistes / 1000 hab | 60 % | 0/1000 = 0pts · ≥2/1000 = 100pts |
| Distance gare SNCF la plus proche | 40 % | 0km = 100pts · ≥30km = 0pts |

**Sources** : SIRENE section Q (médecins, paginé) · SNCF gares voyageurs

**Limites connues** :
- SIRENE compte les **établissements** (cabinets, SELARLs), pas les praticiens individuels. Un cabinet de groupe compte pour 1.
- La distance gare est à vol d'oiseau (OSRM pour la distance routière n'est pas utilisée ici)
- Ne prend pas en compte les commerces, pharmacies, hôpitaux, équipements sportifs

---

### 🌿 Cadre de vie

Moyenne simple de 3 sous-indicateurs :

| Sous-indicateur | Formule |
|----------------|---------|
| Qualité de l'air (EAQI) | EAQI 1 (Bon) = 100pts · EAQI 5 (Très mauvais) = 0pts |
| Fibre optique | % des logements couverts FTTH (ARCEP) |
| Risques naturels/industriels | 0 risques = 100pts · ≥20 risques = 0pts |

**Sources** : Open-Meteo (EAQI temps réel) · ARCEP commune_debit_filaire · GéoRisques GASPAR

**Limites connues** :
- L'EAQI est la valeur **du jour de la collecte** (pas une moyenne annuelle)
- Les risques sont comptés en nombre brut — un risque mineur compte autant qu'un risque majeur

---

## Points à améliorer (backlog scoring)

- [ ] **Sécurité** : normaliser par catégorie vs moyenne nationale avant d'agréger
- [ ] **Éducation** : intégrer la valeur ajoutée si données disponibles, pondérer public vs privé
- [ ] **Immobilier** : traiter les communes avec peu de transactions (bruit statistique)
- [ ] **Services** : ajouter hôpitaux, pharmacies, commerces de proximité
- [ ] **Revenus** : intégrer revenus médians FiLoSoFi INSEE dès que le dataset Melodi est disponible
- [ ] **Comparateur** : afficher 2-3 communes côte à côte avec radar chart des 5 dimensions

---

*Données publiques — Sources : DVF, RPLS, GéoRisques, Éducation nationale, SNCF, OFGL, SIRENE, OSM, SSMSI, ARCEP, Open-Meteo, INSEE Melodi*
