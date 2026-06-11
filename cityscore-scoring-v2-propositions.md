# CityScore — Propositions de scoring v2, nouvelles sources & roadmap

> Document de travail basé sur l'analyse de la doc projet (scoring v1, sources actuelles, limites connues et backlog).

---

## Partie 1 — Propositions de scoring

Trois approches sont proposées car elles répondent à des problèmes **différents** et sont **complémentaires** :
la proposition A corrige les défauts méthodologiques de la v1, la B change la philosophie de normalisation, la C change la philosophie du produit. A est le socle ; B et C se construisent dessus.

---

### Proposition A — « CityScore v2 Robuste » (correction méthodologique, même structure)

On garde les 5 dimensions et la pondération globale (25/25/20/15/15), mais on corrige chaque sous-score connu pour être biaisé. C'est la proposition à implémenter en premier.

#### 🏠 Immobilier (25 %)

| Sous-indicateur | Poids v1 | Poids v2 | Changement |
|---|---|---|---|
| Évolution des prix | 50 % | 35 % | **Lissage bayésien** : si < 20 transactions/an, l'évolution communale est tirée vers la médiane de l'EPCI (ou du département) proportionnellement au déficit de transactions. `evo_corrigée = (n·evo_commune + k·evo_epci) / (n + k)` avec k ≈ 20. Élimine le bruit des petites communes sans les exclure. |
| Prix absolu | 20 % | 25 % | Conservé, mais comparé au **revenu médian local** (cf. FiLoSoFi, Partie 2) dès que disponible : un ratio prix/revenu est plus parlant qu'un seuil national fixe. |
| Taux HLM | 30 % | 15 % | Poids réduit : c'est un proxy social déjà capturé par l'IPS et FiLoSoFi. Courbe adoucie (0 pt seulement au-delà de 40 %). |
| **Vacance des logements** (nouveau) | — | 15 % | Données LOVAC / RP INSEE. Une vacance > 12 % signale un marché en déclin — c'est exactement le signal manquant noté dans les limites v1 (« un prix très bas peut signifier une commune en déclin »). |
| **Tension locative / dynamisme** (nouveau) | — | 10 % | Nb de mutations DVF / 1000 logements : mesure la liquidité du marché (facile à revendre ou non). Donnée déjà collectée. |

#### 🎓 Éducation (25 %)

| Sous-indicateur | Poids v1 | Poids v2 | Changement |
|---|---|---|---|
| IPS moyen | 40 % | 25 % | **Pondérer par effectifs** et séparer public/privé : score = moyenne pondérée avec un coefficient correcteur de −5 pts d'IPS pour le privé sous contrat (biais structurel documenté en v1). |
| DNB réussite | 25 % | 15 % | Conservé. |
| DNB mentions B+TB | 25 % | 15 % | Conservé. |
| **Valeur ajoutée des lycées (IVAL)** (nouveau) | — | 30 % | Dataset `fr-en-indicateurs-de-resultat-des-lycees` sur data.education.gouv.fr : taux de réussite/mention **attendus vs constatés**. C'est exactement la « valeur ajoutée » identifiée comme manquante en v1 — elle existe pour les lycées. Fallback sur le lycée de secteur le plus proche pour les communes sans lycée. |
| Densité scolaire | 10 % | 15 % | Étendre aux distances : pénalité si l'école/collège le plus proche est à > 10 km (rural). |

#### 🛡 Sécurité (20 %) — refonte complète

Le cumul brut des taux est remplacé par une **normalisation par catégorie vs distribution nationale** (le point n°1 du backlog) :

1. Pour chaque catégorie SSMSI *c* : `z_c = (taux_commune_c − médiane_nationale_c) / écart_interquartile_c`
2. Score catégorie : `100 − clamp(z_c, −1, +3) × 25` (une commune à la médiane = 75 pts, très au-dessus = 0).
3. Agrégation pondérée par **gravité** : violences aux personnes ×3, cambriolages ×2, vols simples ×1, autres ×1.
4. **Lissage rural** : communes < 2 000 hab → moyenner avec le taux de l'EPCI (le « 0 donnée = score nul » de la v1 devient « 0 donnée = score EPCI »), et moyenner sur 3 ans glissants pour réduire le bruit.

#### 🏥 Services (15 %)

| Sous-indicateur | Poids v2 | Détail |
|---|---|---|
| Densité médicale | 35 % | Remplacer SIRENE par l'**annuaire santé FINESS / CNAM** (compte les praticiens, pas les établissements — corrige la limite v1). En attendant : garder SIRENE mais ajouter pharmacies (code APE 47.73Z). |
| Accès hôpital / urgences | 15 % | FINESS : distance au service d'urgences le plus proche. 0–15 km = 100 pts, ≥ 60 km = 0. |
| Gare SNCF | 20 % | Passer à la **distance routière OSRM** (déjà dans la stack pour Lyon) au lieu du vol d'oiseau. |
| Commerces & équipements du quotidien | 30 % | **BPE INSEE** (Base Permanente des Équipements) : présence boulangerie, supermarché, pharmacie, poste, école de musique, équipements sportifs… Score = % d'une liste de ~15 équipements « essentiels » présents dans la commune ou à < 10 min. |

#### 🌿 Cadre de vie (15 %)

| Sous-indicateur | Poids v2 | Détail |
|---|---|---|
| Qualité de l'air | 30 % | Remplacer l'EAQI du jour par la **moyenne annuelle** (Open-Meteo Air Quality propose l'historique) — corrige la limite v1. |
| Fibre FTTH | 20 % | Conservé + couverture mobile 4G/5G ARCEP en bonus. |
| Risques | 30 % | Pondérer par gravité au lieu du comptage brut : inondation/industriel Seveso ×3, sismicité ×2, autres ×1 ; et croiser avec la **fréquence d'arrêtés CatNat** GASPAR (un risque théorique jamais réalisé ≠ 4 inondations en 10 ans). |
| Climat (nouveau) | 20 % | Ensoleillement annuel + jours de pluie via Open-Meteo historical (gratuit, déjà dans la stack). Critère majeur de choix de vie totalement absent de la v1. |

---

### Proposition B — Normalisation par percentiles nationaux (« score relatif »)

**Problème visé** : les formules v1/A reposent sur des seuils arbitraires (« 1 500–5 000 €/m² = zone optimale », « 2 médecins/1000 = 100 pts »). Ces seuils sont discutables et devront être maintenus à la main.

**Principe** : pré-calculer chaque indicateur brut pour **toutes les communes de France** (batch nocturne via `collect.py`), puis scorer chaque commune par son **rang percentile** : être au 80ᵉ percentile national de densité médicale = 80 pts, point final.

| Avantages | Inconvénients |
|---|---|
| Zéro seuil arbitraire à justifier | Nécessite un pré-calcul national (≈ 35 000 communes) — changement d'architecture vs collecte à la demande |
| Auto-calibrant dans le temps (si toute la France se fibre, le critère se durcit seul) | Un percentile masque les valeurs absolues (« meilleur que 90 % » peut rester objectivement mauvais) |
| Permet d'afficher « top X % national » — très lisible pour l'utilisateur | |

**Recommandation** : hybride. Garder les formules absolues de la proposition A pour le score, mais **afficher le percentile national à côté de chaque sous-score** (« 7,2 €/m² — 88ᵉ percentile du département »). Le percentile devient un élément d'UX avant de devenir, éventuellement, le moteur de scoring en v3.

---

### Proposition C — Score personnalisé par profil (« MonCityScore »)

**Problème visé** : la pondération 25/25/20/15/15 suppose un utilisateur moyen qui n'existe pas. Un retraité se moque de l'IPS des collèges ; un télétravailleur de 28 ans se moque des urgences pédiatriques.

**Principe** : les 5 dimensions (et leurs sous-scores) restent calculés comme en A, mais la pondération finale devient un vecteur choisi par l'utilisateur, avec 4 presets :

| Profil | 🏠 Immo | 🎓 Éduc | 🛡 Sécu | 🏥 Services | 🌿 Cadre |
|---|---|---|---|---|---|
| Famille avec enfants | 20 % | 35 % | 20 % | 15 % | 10 % |
| Télétravailleur | 25 % | 5 % | 15 % | 20 % | 35 % |
| Retraité | 20 % | 0 % | 20 % | 35 % | 25 % |
| Jeune actif / investisseur | 40 % | 10 % | 15 % | 20 % | 15 % |

Implémentation triviale (le recalcul est une somme pondérée côté serveur ou même en JS), impact produit majeur : c'est ce qui différencie CityScore d'un simple annuaire de données. Le CityScore « par défaut » (pondération actuelle) reste affiché comme référence comparable entre utilisateurs.

---

## Partie 2 — Sources de données supplémentaires (toutes open data, gratuites)

### Priorité haute (combler les trous identifiés dans la doc)

| Source | Données | Usage CityScore |
|---|---|---|
| **INSEE FiLoSoFi** (Melodi ou fichiers data.gouv) | Revenu médian, taux de pauvreté, déciles par commune | Ratio prix immobilier/revenu, contexte social — déjà dans votre backlog |
| **INSEE BPE** (Base Permanente des Équipements) | ~2 600 types d'équipements géolocalisés : commerces, sport, culture, santé, services publics | Refonte complète du score Services (boulangerie, supermarché, piscine, cinéma, bureau de poste…) |
| **FINESS / Annuaire Santé** (data.gouv) | Hôpitaux, urgences, maternités, EHPAD, pharmacies, praticiens | Remplace SIRENE (compte les praticiens, pas les sociétés) |
| **IVAL — Indicateurs de résultats des lycées** (data.education.gouv.fr) | Taux attendus vs constatés au bac, par lycée | La « valeur ajoutée » pédagogique manquante en v1 |
| **LOVAC / RP INSEE — logements vacants** | Taux de vacance par commune | Détecteur de communes en déclin |
| **transport.data.gouv.fr** (GTFS) | Lignes et arrêts de bus/tram/TER, fréquences | Score mobilité réel (la gare seule ne dit rien de la desserte) |
| **Open-Meteo Historical** (déjà dans la stack) | Ensoleillement, précipitations, températures, moyennes annuelles air | Sous-score climat + correction EAQI « du jour » |

### Priorité moyenne (enrichissement des fiches)

| Source | Données | Usage |
|---|---|---|
| **Hub'Eau — qualité de l'eau potable** | Conformité bactériologique/chimique par réseau | Cadre de vie / santé |
| **ARCEP Mon Réseau Mobile** | Couverture 4G/5G par opérateur | Complément fibre, crucial en rural |
| **BASOL / BASIAS** (Géorisques) | Sites et sols pollués | Complément risques |
| **Cartes stratégiques de bruit** (PPBE, data.gouv) | Exposition au bruit routier/ferroviaire/aérien | Cadre de vie — fort impact ressenti, jamais couvert |
| **Corine Land Cover / OSO Theia** | % espaces verts, forêts, artificialisation | Verdure réelle de la commune |
| **CAF data.caf.fr + annuaire crèches** | Places en crèche, modes de garde | Score famille (préempte la proposition C) |
| **INSEE emploi / SIRENE créations** | Taux de chômage de la zone d'emploi, créations d'entreprises | Dynamisme économique local |
| **DPE ADEME** (data.ademe.fr) | Étiquettes énergétiques des logements par commune | Qualité du parc, passoires thermiques |
| **RNA — Répertoire National des Associations** | Nb d'associations actives / 1000 hab | Vitalité sociale et vie locale |
| **data.gouv — marchés / labels** | Marchés hebdomadaires, labels « Villes et villages fleuris », « Petites villes de demain » | Éléments qualitatifs de fiche |

### Priorité basse / différenciant

| Source | Données | Usage |
|---|---|---|
| **IGN (altitude, BD TOPO)** | Altitude, distance littoral/montagne/lac | Filtres « bord de mer », « montagne » |
| **Météo-France DRIAS** | Projections climatiques 2050 (canicules, sécheresse) | Argument différenciant : « votre commune en 2050 » |
| **Données électorales (data.gouv)** | Participation, résultats | À manier avec prudence ; la participation électorale est un proxy d'engagement local acceptable |
| **Observatoire des territoires (ANCT)** | Centaines d'indicateurs pré-agrégés par commune | Source de secours / validation croisée |
| **SNCF horaires (GTFS)** | Temps de trajet réel vers Paris/Lyon/grandes villes | Remplacer « distance gare » par « temps de trajet vers la métropole » — bien plus pertinent pour les télétravailleurs |

---

## Partie 3 — Axes d'amélioration pour les versions ultérieures

### Scoring & données
1. **Indicateur de confiance par fiche** : chaque sous-score affiche un badge (●●● données complètes / ●○○ données partielles ou extrapolées EPCI). Indispensable dès que le lissage rural (prop. A) est en place — sinon l'utilisateur ne sait pas ce qui est mesuré vs estimé.
2. **Historisation** : stocker les collectes au-delà du cache 7 jours pour afficher des tendances (« la criminalité baisse depuis 3 ans », « les prix accélèrent ») — la donnée temporelle est plus convaincante qu'un score figé.
3. **Batch national** (prérequis de la proposition B) : pré-calculer les indicateurs bruts des 35 000 communes une fois par mois, et ne garder la collecte à la demande que pour les données temps réel (air).
4. **Pondération par gravité documentée** : publier la méthodologie complète sur une page `/methodologie` — la transparence est votre principal atout face aux classements de magazines.

### Produit
5. **Comparateur** (déjà au backlog) : 2–3 communes côte à côte, radar chart des 5 dimensions + écarts sous-score par sous-score.
6. **Recherche inversée** : « je veux : < 2 500 €/m², lycée à valeur ajoutée positive, < 1 h de Lyon en train, fibre » → liste des communes qui matchent. C'est le vrai produit ; la fiche n'est que la preuve. Nécessite le batch national (point 3).
7. **Carte interactive** : choroplèthe du CityScore (ou d'une dimension) par département/EPCI, clic → fiche.
8. **Profils & pondérations personnalisées** (proposition C) avec partage d'URL (`?profil=famille` ou poids encodés dans l'URL).
9. **Alertes** : « prévenez-moi si une commune de ma sélection passe sous X €/m² » (nécessite l'historisation, point 2).
10. **Fiches voisines** : sur chaque fiche, suggérer 3 communes voisines au meilleur score — favorise l'exploration et le temps passé sur le site.

### Technique
11. **Base de données** (SQLite suffit largement) à la place des JSON par dossier dès que le batch national arrive ; les JSON deviennent ingérables à 35 000 communes.
12. **Tests de non-régression du scoring** : un jeu de ~20 communes de référence (Paris, village rural, banlieue, ville moyenne…) avec scores attendus, relancé à chaque modification de formule.
13. **Gestion des API en panne** : statut par source dans la fiche (la v1 score « 0 » quand SSMSI ne répond pas — il faut distinguer « donnée absente » de « mauvais score »).
14. **Versionner le scoring** : afficher « CityScore v2.1 » sur les fiches et dater les collectes, pour que deux fiches consultées à 6 mois d'écart restent interprétables.

---

### Synthèse des recommandations

1. Implémenter la **proposition A** (corrige les 8 limites documentées en v1, dont 3 déjà dans votre backlog) avec en priorité : refonte sécurité, IVAL lycées, BPE pour les services, climat.
2. Ajouter le **percentile national en affichage** (proposition B allégée) une fois le batch national en place.
3. Lancer les **profils de pondération** (proposition C) — coût quasi nul, différenciation maximale.
4. Côté données : **FiLoSoFi, BPE, FINESS, IVAL, GTFS** sont les 5 ajouts au meilleur ratio effort/valeur.
