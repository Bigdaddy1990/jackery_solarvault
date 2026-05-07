# Intégration Home Assistant Jackery SolarVault 3 Pro Max

**🌍 Language / Sprache / Idioma / Langue :**
[🇬🇧 English](../README.md) · [🇩🇪 Deutsch](./README.de.md) · [🇫🇷 Français](./README.fr.md) · [🇪🇸 Español](./README.es.md)

---

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)


Intégration communautaire pour les systèmes Jackery SolarVault, en particulier SolarVault 3 Pro Max. L'intégration lit les valeurs en direct, les statistiques d'énergie et les paramètres configurables depuis le cloud Jackery, et utilise le push MQTT pour les changements d'état rapides et les commandes de contrôle.

> ⚠️ Cette intégration n'est pas un produit officiel Jackery et n'a aucun lien avec Jackery Inc.


## Fonctionnalités

- Détection automatique des appareils et du système via le compte Jackery
- Actualisation HTTP régulière des valeurs standard avec un intervalle fixe de 30 secondes
- Push MQTT pour l'état en direct, le smart meter, les batteries d'extension et les commandes de contrôle
- Appareil principal, smart meter et batteries d'extension comme appareils Home Assistant séparés
- Prise en charge de jusqu'à 5 batteries d'extension
- Puissance en direct : batterie, PV total, canaux PV, import réseau, export réseau, EPS et pile de batteries d'extension
- Statistiques d'énergie : jour, semaine, mois et année pour le PV, la consommation et la batterie
- Valeurs à long terme compatibles avec le tableau de bord Énergie uniquement pour les valeurs cumulatives totales/journalières ; les valeurs hebdomadaires/mensuelles/annuelles sont de simples valeurs d'affichage
- Puissance du smart meter avec valeurs par phase si un smart meter est connecté
- Configuration via des entités : EPS, limites de charge/décharge, limite de puissance d'injection, puissance de sortie maximale, mode de consommation d'énergie, arrêt automatique, suivi du smart meter, alerte tempête, unité de température, prix de l'électricité et veille
- Bouton de redémarrage de l'appareil
- Entités de diagnostic pour l'état en ligne, le firmware, les limites système, la norme réseau, le code pays, les données brutes et l'état MQTT

## Installation via HACS

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)

1. Ouvrir HACS.
2. Ouvrir le menu à trois points en haut à droite.
3. Sélectionner `Custom repositories`.
4. Saisir l'URL du dépôt `https://github.com/Bigdaddy1990/jackery_solarvault` et choisir la catégorie `Integration`.
5. Rechercher `Jackery SolarVault` et l'installer.
6. Redémarrer Home Assistant.
7. Aller dans Paramètres → Appareils et services → Ajouter une intégration → `Jackery SolarVault`.

## Installation manuelle

1. Télécharger le ZIP depuis la [page des releases](https://github.com/Bigdaddy1990/jackery_solarvault/releases).
2. Copier le dossier `custom_components/jackery_solarvault` dans `<HA-config>/custom_components/`.
3. Redémarrer Home Assistant.
4. Ajouter l'intégration via Paramètres → Appareils et services.

## Configuration

Requis :

- adresse e-mail du cloud Jackery
- mot de passe du cloud Jackery
- facultatif : activer/désactiver les capteurs smart meter calculés
- facultatif : activer/désactiver les capteurs de puissance calculés

L'ID de l'appareil, l'ID système, la macId MQTT et la région sont déduits des données cloud/MQTT et ne sont plus demandés manuellement dans l'interface utilisateur.

## Remarque importante concernant la connexion Jackery

Jackery n'autorise en pratique qu'une seule session active par compte. Si l'application officielle et Home Assistant sont connectés simultanément avec le même compte, les jetons et les identifiants MQTT peuvent être renouvelés. Cela peut entraîner l'expiration de jetons ou des erreurs d'authentification MQTT.

Recommandé :

1. Créer un deuxième compte Jackery.
2. Partager le SolarVault avec le deuxième compte dans l'application Jackery via le partage/code QR.
3. Utiliser le deuxième compte dans Home Assistant.

## Entités

### Capteurs standard

- SOC total et batterie interne
- Puissance de charge de la batterie et puissance de décharge de la batterie
- Puissance PV totale et canaux PV 1-4
- Import réseau, export réseau et puissance réseau nette
- Puissance d'entrée/sortie côté réseau
- Puissance EPS
- Puissance de charge/décharge des batteries d'extension
- Autre puissance de charge
- Prix de l'électricité
- Alarmes actives
- Valeurs journalières/hebdomadaires/mensuelles/annuelles pour le PV, la consommation et la batterie

### Batteries d'extension

Les batteries d'extension sont créées séparément de l'appareil principal. Pour chaque batterie détectée, les informations suivantes sont affichées lorsque disponibles :

- SOC
- Température des cellules
- Puissance de charge
- Puissance de décharge
- Version du firmware
- État de communication sous forme d'attributs

### Smart meter

Le smart meter est créé comme appareil distinct sous le SolarVault. Valeurs prises en charge :

- Puissance totale
- Puissance phase 1
- Puissance phase 2
- Puissance phase 3
- Valeurs brutes disponibles sous forme d'attributs

### Entités configurables

- Sortie EPS
- Veille
- Arrêt automatique en mode îlot (avec durée d'arrêt automatique)
- Limite de charge et de décharge
- Limite de puissance d'injection
- Puissance de sortie maximale
- Puissance de sortie standard
- Suivi du smart meter
- Mode de consommation d'énergie
- Mode de prix de l'électricité
- Prix au tarif unique
- Unité de température
- Alerte tempête et délai de préalerte
- Redémarrage

## Services

L'intégration enregistre trois services dans l'espace de noms `jackery_solarvault` :

| Service | Objectif |
|---|---|
| `jackery_solarvault.rename_system` | Renommer le système (appareil SolarVault) dans le cloud |
| `jackery_solarvault.refresh_weather_plan` | Récupérer le plan actuel d'alerte tempête depuis le serveur cloud |
| `jackery_solarvault.delete_storm_alert` | Supprimer une alerte tempête active via une commande cloud |

Pour plus de détails sur les paramètres requis, voir `services.yaml` ou l'éditeur Services dans les outils de développement HA.

## Bien lire les capteurs d'énergie et de puissance

- La puissance de décharge de la batterie indique ce que la batterie fournit.
- Le réseau net correspond à l'import réseau moins l'export réseau. Cette valeur n'a pas forcément à correspondre à la puissance de décharge de la batterie, car la charge de la maison, le PV, le smart meter et la régulation interne se trouvent entre les deux.
- L'entrée/sortie de la pile se rapporte à la pile de batteries d'extension ou au flux de puissance entre l'appareil principal et les batteries d'extension.
- Les valeurs du smart meter proviennent du compteur connecté et sont gérées séparément des valeurs de l'appareil principal.
- Le capteur `Consommation actuelle de la maison` calcule la consommation instantanée à partir de la consommation domestique en direct signalée par Jackery (`otherLoadPw`) et n'utilise la puissance nette du smart meter moins l'entrée côté réseau Jackery plus la sortie côté réseau Jackery qu'en solution de secours. Cela évite que l'injection du SolarVault soit déduite à tort de la consommation de la maison.
- Les capteurs d'énergie journaliers/hebdomadaires/mensuels/annuels utilisent `state_class: total` avec le `last_reset` adapté à la période d'application correspondante. Ce sont des valeurs de période, pas des compteurs à vie croissants de façon monotone.
- Les valeurs hebdomadaires, mensuelles et annuelles sont calculées de façon identique à partir de la série de graphique correspondante de l'application. La série dépend du payload : les valeurs totales de tendance PV/maison utilisent généralement `y`, la charge/décharge de la batterie utilise `y1`/`y2`, l'entrée/sortie côté réseau de l'appareil utilise `y1`/`y2`, et PV1..PV4 utilisent `y1`..`y4`. Les champs de total serveur ne sont plus utilisés que comme valeurs de secours/diagnostic, car les champs de total mensuels/annuels peuvent être trompeurs selon le payload.

### Périodes, totaux et avertissements

- Semaine = lundi à dimanche.
- Mois = mois calendaire.
- Année = année calendaire.
- Les valeurs totales/lifetime proviennent des champs totaux documentés de l'application/HTTP/MQTT et ne sont pas assemblées à partir des valeurs hebdomadaires, mensuelles ou annuelles.
- Il n'y a explicitement aucune utilisation des valeurs hebdomadaires pour réparer des valeurs mensuelles, annuelles ou totales, ni des valeurs mensuelles pour réparer des valeurs annuelles ou totales.
- Au début d'un mois, la valeur hebdomadaire peut être supérieure à la valeur mensuelle si la semaine en cours contient encore des jours du mois précédent. Ce n'est pas un bug.
- Si Jackery fournit des données contradictoires, par exemple une valeur annuelle inférieure à une semaine complète située dans la même année ou une production totale inférieure à la production annuelle, l'intégration ne modifie pas silencieusement les valeurs des entités. Elle crée plutôt une notification de réparation et stocke les détails dans l'export de diagnostic sous `data_quality`.

## Polling et mises à jour

Le polling HTTP rapide s'exécute avec un intervalle fixe de 30 secondes. Les statistiques cloud lentes sont volontairement interrogées moins souvent, car Jackery ne met pas ces données à jour côté serveur chaque seconde.

Le push MQTT met à jour les valeurs en direct indépendamment du polling dès que le broker est connecté.

La connexion TLS MQTT vérifie activement la chaîne de certificats du broker. Le fichier ``custom_components/jackery_solarvault/jackery_ca.crt`` est fourni comme ancre de confiance documentée pour ``emqx.jackeryapp.com``, car Jackery ne fait pas signer le broker par une CA publique. Avec Python 3.10+/OpenSSL 3.x, le drapeau strict ``VERIFY_X509_STRICT`` est également désactivé de manière ciblée, car le certificat serveur ne fournit pas l'extension ``Authority Key Identifier``. La vérification du nom d'hôte, la vérification de chaîne et la vérification de signature restent actives (``CERT_REQUIRED`` + ``check_hostname = True``). Il n'existe aucun repli automatique vers ``tls_insecure`` ou ``CERT_NONE`` — les erreurs TLS restent visibles. L'export de diagnostic affiche notamment ``tls_custom_ca_loaded``, ``tls_x509_strict_disabled`` et ``tls_certificate_source`` sous ``mqtt_status``, afin que la configuration TLS soit compréhensible sans journalisation de débogage. Le contexte et les règles de modification de cette stratégie sont documentés dans ``docs/STRICT_WORK_INSTRUCTIONS.md``.

Les données de diagnostic MQTT ne contiennent que des chemins de topics expurgés (`hb/app/**REDACTED**/...`), des compteurs et des horodatages pour la connexion, le dernier message, la dernière publication et les payloads rejetés. La partie `userId` Jackery du topic n'est pas incluse dans l'export de diagnostic.

## Journalisation de débogage

Pour l'analyse des erreurs :

```yaml
logger:
  default: info
  logs:
    custom_components.jackery_solarvault: debug
```

## Prérequis

- Home Assistant 2025.8.0 ou plus récent
- Python 3.13+ (fourni par Home Assistant)
- Compte cloud Jackery
- SolarVault en ligne via Wi-Fi ou Ethernet
- HACS pour l'installation recommandée

## Contribuer

Veuillez soumettre les rapports de bugs et les demandes de fonctionnalités via les [GitHub Issues](https://github.com/Bigdaddy1990/jackery_solarvault/issues). En cas de problèmes d'authentification ou MQTT, un export de diagnostic depuis HA (Paramètres → Appareils et services → Jackery SolarVault → trois points → Télécharger les diagnostics) est très utile. Les champs sensibles sont automatiquement expurgés ; vérifiez tout de même brièvement un export de diagnostic avant de le partager.

## Licence

Licence MIT. Voir [LICENSE](../LICENSE).

## Calculation details

Savings calculation and cloud-value guards are documented in [`APP_CLOUD_VALUES.md`](APP_CLOUD_VALUES.md).
