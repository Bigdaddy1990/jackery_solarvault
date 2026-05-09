# Jackery SolarVault pour Home Assistant

Langues :
[English](../README.md) · [Deutsch](./README.de.md) · [Français](./README.fr.md) · [Español](./README.es.md)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)

Intégration communautaire pour les systèmes Jackery SolarVault, en particulier SolarVault 3 Pro Max. Elle lit les valeurs en direct, les statistiques d'énergie et les paramètres configurables depuis le cloud Jackery, et utilise le push MQTT pour les mises à jour rapides et les commandes de contrôle.

Cette intégration n'est pas un produit officiel Jackery et n'est pas affiliée à Jackery Inc.

## Ce que fournit l'intégration

- Détection automatique du système et des appareils via le compte cloud Jackery.
- Unité principale, smart meter et batteries d'extension comme appareils Home Assistant séparés.
- Capteurs de puissance en direct pour la batterie, le PV total, les canaux PV, l'import/export réseau, l'EPS, la puissance de pile et les phases du smart meter.
- Capteurs d'énergie pour les périodes de l'application Jackery : jour, semaine, mois et année.
- Entités configurables pour l'EPS, la veille, les limites, la puissance de sortie, le suivi du smart meter, l'alerte tempête, l'unité de température et le prix de l'électricité.
- Bouton de redémarrage de l'appareil et services cloud pour le nom du système et la gestion des alertes tempête.
- Diagnostics pour les données brutes expurgées, l'état MQTT, le firmware, les limites système et les avertissements de qualité des données.

## Prérequis

- Home Assistant 2025.8.0 ou plus récent.
- Python 3.14 ou plus récent, fourni par Home Assistant.
- Un compte cloud Jackery.
- SolarVault en ligne via Wi-Fi ou Ethernet.
- HACS pour la méthode d'installation recommandée.

## Configuration de compte Jackery recommandée

Jackery n'autorise en pratique qu'une seule session active par compte. Si l'application officielle Jackery et Home Assistant utilisent le même compte en même temps, les jetons et identifiants MQTT peuvent être renouvelés. Cela peut provoquer des erreurs de jeton expiré, des erreurs d'authentification MQTT ou des données temporairement obsolètes.

Configuration recommandée :

1. Créer un deuxième compte Jackery.
2. Partager le SolarVault avec ce deuxième compte dans l'application Jackery.
3. Utiliser ce deuxième compte uniquement pour Home Assistant.

## Installation

### HACS

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)

1. Ouvrir HACS.
2. Ouvrir le menu à trois points.
3. Sélectionner `Custom repositories`.
4. Ajouter `https://github.com/Bigdaddy1990/jackery_solarvault` comme `Integration`.
5. Rechercher `Jackery SolarVault` et l'installer.
6. Redémarrer Home Assistant.
7. Aller dans `Paramètres > Appareils et services > Ajouter une intégration`.
8. Sélectionner `Jackery SolarVault`.

### Manuelle

1. Télécharger le ZIP depuis la [page des releases](https://github.com/Bigdaddy1990/jackery_solarvault/releases).
2. Copier `custom_components/jackery_solarvault` dans `<HA-config>/custom_components/`.
3. Redémarrer Home Assistant.
4. Ajouter `Jackery SolarVault` depuis `Paramètres > Appareils et services`.

## Configuration et options

Le flux de configuration demande :

- L'adresse e-mail du cloud Jackery.
- Le mot de passe du cloud Jackery.
- Si les capteurs calculés du smart meter doivent être créés.
- Si les capteurs calculés de puissance nette doivent être créés.
- Si les capteurs de détail du calcul d'économies doivent être créés.

L'ID d'appareil, l'ID système, le `macId` MQTT et la région sont déduits des données cloud et MQTT. Ils ne sont pas saisis manuellement.

Les mêmes options peuvent être modifiées plus tard dans les options de l'intégration. Les identifiants peuvent être mis à jour via les flux de reconfiguration ou de réauthentification de Home Assistant sans supprimer l'intégration.

## Appareils et entités

### Appareil SolarVault principal

Capteurs typiques :

- État de charge.
- Puissance de charge et de décharge de la batterie.
- Puissance PV totale et puissance PV1 à PV4.
- Import réseau, export réseau et puissance réseau nette.
- Puissance d'entrée et de sortie côté réseau.
- Puissance EPS.
- Puissance de charge et de décharge de la pile.
- Autre puissance de charge.
- Prix de l'électricité.
- Valeurs d'application jour/semaine/mois/année.
- Nombre d'alarmes actives.

Contrôles typiques :

- Sortie EPS.
- Veille.
- Arrêt automatique en mode hors réseau et délai d'arrêt automatique.
- Limites de charge et de décharge.
- Limite de puissance d'injection.
- Puissance de sortie maximale.
- Puissance de sortie par défaut.
- Suivi du smart meter.
- Mode de consommation d'énergie.
- Mode de prix et prix forfaitaire.
- Unité de température.
- Alerte tempête et délai de préalerte.
- Redémarrage.

### Batteries d'extension

Les batteries d'extension sont créées comme appareils séparés lorsque Jackery fournit leurs données. Jusqu'à cinq batteries sont prises en charge. Selon le payload, chaque batterie peut exposer :

- État de charge.
- Température des cellules.
- Puissance de charge et de décharge.
- Version du firmware.
- Numéro de série.
- État de communication sous forme d'attributs.

### Smart meter

Lorsqu'un smart meter Jackery est connecté, il est créé comme appareil séparé. Il peut exposer :

- Puissance totale du compteur.
- Puissance phase 1, phase 2 et phase 3.
- Attributs bruts du compteur pour le diagnostic.
- Capteurs calculés de consommation du foyer lorsque l'option est activée.

## Services

L'intégration enregistre ces services sous `jackery_solarvault` :

| Service | Objectif |
|---|---|
| `jackery_solarvault.rename_system` | Renommer le système SolarVault dans le cloud Jackery |
| `jackery_solarvault.refresh_weather_plan` | Récupérer le plan actuel d'alerte tempête |
| `jackery_solarvault.delete_storm_alert` | Supprimer une alerte tempête active via une commande cloud |

Utilisez `Outils de développement > Actions` dans Home Assistant pour les paramètres des services. Les actions `refresh_weather_plan` et `delete_storm_alert` affichent un sélecteur d'appareil filtré sur les appareils Jackery : choisissez l'unité principale SolarVault. Les automatisations peuvent aussi transmettre directement le `device_id` numérique brut de Jackery, visible dans l'export de diagnostic. `rename_system` conserve un champ texte parce qu'un système Jackery couvre plusieurs appareils Home Assistant et est identifié par l'ID système numérique dans les diagnostics.

Lorsque deux comptes Jackery sont configurés, chaque action est automatiquement routée vers l'entrée cloud qui possède l'ID système ou appareil demandé.

## Tableau de bord Énergie et signification des capteurs

Utilisez les capteurs d'énergie avec attention. Jackery expose plusieurs valeurs qui semblent proches mais qui n'ont pas la même signification.

- La puissance de décharge de la batterie indique ce que la batterie fournit.
- La puissance réseau nette correspond à l'import réseau moins l'export réseau. Elle ne doit pas forcément correspondre à la puissance de décharge de la batterie, car le PV, la charge du foyer, les valeurs du smart meter et la régulation interne se trouvent entre les deux.
- L'entrée/sortie de pile décrit la pile de batteries d'extension ou le flux de puissance entre l'unité principale et les batteries d'extension.
- Les valeurs du smart meter proviennent du compteur connecté et sont traitées séparément des valeurs de l'unité principale.
- `Consommation actuelle du foyer` utilise la charge du foyer en direct de Jackery (`otherLoadPw`) lorsqu'elle est disponible. Si cette valeur manque, l'intégration utilise en secours la puissance nette du smart meter moins l'entrée côté réseau Jackery plus la sortie côté réseau Jackery.
- `Sortie réseau quotidienne (cloud Jackery)` correspond au champ Jackery `todayLoad`. Ce n'est pas une mesure fiable de la consommation réelle du foyer. Pour la consommation du foyer, utilisez les capteurs calculés de smart meter/consommation du foyer lorsqu'ils sont disponibles.
- `Économies totales de l'application` est le KPI brut de l'application Jackery. Il peut ressembler à un revenu PV. `Économies calculées` est l'estimation locale basée sur l'énergie AC autoconsommée, l'entrée/sortie côté réseau, l'export public optionnel, la consommation du foyer et le prix d'électricité configuré.

Pour la configuration du tableau de bord Énergie de Home Assistant, privilégiez les vraies valeurs cumulatives/journalières et les capteurs calculés de consommation du foyer. Ne traitez pas les capteurs de période semaine, mois ou année comme des compteurs de service à vie.

Les détails du calcul d'économies sont documentés dans [`APP_CLOUD_VALUES.md`](APP_CLOUD_VALUES.md).

## Règles de période et qualité des données

L'intégration utilise les mêmes limites de période locales que l'application Jackery :

- Semaine : lundi à dimanche.
- Mois : mois calendaire.
- Année : année calendaire.

Comportement important :

- Les capteurs de période sont des totaux de période, pas des compteurs à vie.
- Les valeurs hebdomadaires ne sont pas utilisées pour réparer les valeurs mensuelles, annuelles ou totales.
- Lorsque Jackery renvoie une valeur du mois courant comme valeur annuelle ou totale de production/carbone, l'intégration peut la protéger vers le haut avec des valeurs mensuelles explicites du même endpoint et de la même année calendaire.
- `Économies totales de l'application` reste la valeur cloud brute. La valeur d'économies calculées est séparée.
- Au début d'un mois, une valeur hebdomadaire peut être supérieure à la valeur mensuelle si la semaine en cours inclut des jours du mois précédent. C'est attendu.
- Si Jackery renvoie des données contradictoires qui ne peuvent pas être protégées proprement, l'intégration crée un problème de réparation Home Assistant et stocke les détails dans l'export de diagnostic sous `data_quality`.

## Polling, MQTT et TLS

Le push MQTT est le chemin principal pour les mises à jour en direct dès qu'il est connecté. Le polling HTTP reste utilisé au démarrage, comme secours et comme keep-alive :

- Le rafraîchissement HTTP rapide utilise un intervalle de base de 30 secondes.
- Lorsque MQTT est actif, les cycles HTTP rapides sont ignorés et un rafraîchissement HTTP complet est conservé avec une cadence de keep-alive plus lente.
- Les statistiques cloud lentes ainsi que les données de prix/configuration sont interrogées moins souvent, car le cloud Jackery ne les met pas à jour chaque seconde.

La connexion TLS MQTT vérifie la chaîne de certificats du broker et le nom d'hôte. L'intégration inclut `custom_components/jackery_solarvault/jackery_ca.crt` comme ancre de confiance pour `emqx.jackeryapp.com`, car le certificat du broker Jackery n'est pas signé par une CA publique. Il n'existe aucun fallback automatique vers un TLS non sécurisé. L'état TLS est visible dans l'export de diagnostic.

Les détails techniques du traitement TLS sont documentés dans [`STRICT_WORK_INSTRUCTIONS.md`](STRICT_WORK_INSTRUCTIONS.md).

## Diagnostics et dépannage

Pour les problèmes d'authentification ou de MQTT, téléchargez les diagnostics depuis :

`Paramètres > Appareils et services > Jackery SolarVault > menu à trois points > Télécharger les diagnostics`

Les champs sensibles sont expurgés. Les chemins de topics MQTT sont exportés sous la forme `hb/app/**REDACTED**/...` ; l'ID utilisateur Jackery brut n'est pas inclus. L'export de diagnostic contient aussi des compteurs de payloads rejetés, les horodatages de connexion MQTT et les avertissements de qualité des données.

Activez la journalisation debug normale lors de l'analyse d'un problème :

```yaml
logger:
  default: info
  logs:
    custom_components.jackery_solarvault: debug
```

La journalisation debug des payloads HTTP/MQTT bruts est séparée et volontairement disponible uniquement par opt-in. Elle n'écrit `/config/jackery_solarvault_payload_debug.jsonl` que lorsque ce logger dédié est réglé sur `debug` :

```yaml
logger:
  logs:
    custom_components.jackery_solarvault.payload_debug: debug
```

Le fichier de debug des payloads est limité en fréquence et tourne vers `jackery_solarvault_payload_debug.jsonl.1` à 2 Mo. Sur une installation normale, il n'existe pas.

Les icônes de marque Home Assistant sont chargées depuis le cache de marque local `/homeassistant/.cache/brands/integrations/jackery/` lorsqu'il est disponible.

## Documentation de référence

- [`APP_CLOUD_VALUES.md`](APP_CLOUD_VALUES.md) : valeurs Jackery app/cloud et calcul des économies.
- [`DATA_SOURCE_PRIORITY.md`](DATA_SOURCE_PRIORITY.md) : priorité des sources MQTT, HTTP et statistiques de l'application.
- [`MQTT_PROTOCOL.md`](MQTT_PROTOCOL.md) : topics MQTT et contrats de payload.
- [`APP_POLLING_MQTT.md`](APP_POLLING_MQTT.md) : détails du polling HTTP et MQTT.

## Contribuer

Veuillez soumettre les rapports de bugs et les demandes de fonctionnalités via les [GitHub Issues](https://github.com/Bigdaddy1990/jackery_solarvault/issues). Pour les problèmes d'authentification, de MQTT ou de qualité des données, joignez si possible un export de diagnostic Home Assistant. Les champs sensibles sont automatiquement expurgés, mais vérifiez tout de même le fichier avant de le partager publiquement.

## Licence

Licence MIT. Voir [LICENSE](../LICENSE).
