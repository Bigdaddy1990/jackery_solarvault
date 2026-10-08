# Jackery SolarVault for Home Assistant

Languages:
[English](../README.md) · [Deutsch](./README.de.md) · [Français](./README.fr.md) · [Español](./README.es.md)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)

Une intégration personnalisée pour [Home Assistant](https://www.home-assistant.io/) qui intègre vos stations d'énergie Jackery SolarVault, HomePower et Explorer directement dans votre maison intelligente.

Elle combine les fonctions de l’API cloud Jackery avec la télémétrie et les commandes compatibles via **MQTT local** et **Bluetooth (BLE)** en option. Les fonctions disponibles dépendent de l’appareil, du firmware et du service cloud ; la configuration initiale reste dans l’application Jackery.

---

## 🏆 Pourquoi cette intégration est le meilleur choix

Vous avez peut-être entendu parler d'autres solutions MQTT manuelles ou d'intégrations plus anciennes. Voici pourquoi cette intégration est clairement supérieure :

1. **Aucune extraction manuelle de tokens :** La configuration utilise vos identifiants cloud pour découvrir les appareils et récupérer les clés et les données de session MQTT. Vous n'avez pas à intercepter le trafic réseau ni à créer manuellement des charges utiles JSON.
2. **Contrôle local optionnel :** Sur les appareils compatibles, un courtier MQTT local configuré et le **Bluetooth (BLE)** activé peuvent transmettre des données et des commandes locales. Ces canaux supplémentaires ne remplacent pas la connexion au cloud et ne désactivent ni HTTP ni le MQTT cloud. Cette intégration ne propose pas de configuration entièrement locale.
3. **Fonctions cloud :** L’intégration expose la planification horaire, l’intégration Shelly, les vérifications de firmware et les paramètres de charge avancés lorsque ces fonctions sont prises en charge. Elle n’implémente pas la configuration Wi-Fi initiale via BLE. Consultez [les parcours QR, association et partage (en anglais)](./account-sessions.md#qr-codes-account-binding-and-sharing).

---

## 🔋 Appareils Compatibles

Cette intégration prend en charge une large gamme d'appareils Jackery, notamment :

- **Systèmes d'Énergie Domestique :** Série Jackery SolarVault et HomePower.
- **Stations d'Énergie Portables (Série Explorer) :** E240, E557, E900, E1000, E1500V2, E1800, E2000, E3000, E7647, E7987.
- **Accessoires :** Batteries supplémentaires, compteurs intelligents (Smart Meters), prises intelligentes et prises cloud Shelly liées.

---

## ✨ Fonctionnalités & Entités

L'intégration crée des dizaines d'entités par appareil pour vous donner une visibilité et un contrôle complets :

| Plateforme | Exemples |
|------------|----------|
| `sensor` | SOC, puissance d'entrée/sortie/PV, réseau, températures, autonomie restante, statistiques d'énergie |
| `binary_sensor` | état de charge, en ligne et défauts |
| `number` | limite de puissance de charge, limites de batterie personnalisées, délai de sortie AC |
| `select` | mode de fonctionnement, mode de charge, priorité de sortie, modèle onduleur, mode de prix de l'électricité |
| `switch` | sortie EPS, sorties AC/DC, économie d'énergie, charge super rapide |
| `button` | redémarrer, clignotement de la batterie |
| `text` | identifiants Wi-Fi et diagnostics |

### 🛠️ Services Avancés

Nous exposons également plus de 60 services personnalisés dans Home Assistant, vous donnant la puissance de l'application Jackery dans vos automatisations :
- **Gestion des Appareils :** `bind_device`, `unbind_device`, `get_share_qr_code` (selon l'appareil ; les services de partage ne prouvent pas que SolarVault prend en charge le partage)
- **Cloud-to-Cloud :** `get_shelly_auth_url`, `list_shelly_devices`
- **Planification de l'Énergie :** `save_tou_plan`, `insert_electricity_strategy`, `bind_currency`
- **Statistiques :** `query_charge_report`, `query_soc_stat`, `query_profit_stat`

---

## 🛠️ Installation

### HACS (Recommandé)

1. Ouvrez HACS.
2. Ouvrez le menu à trois points.
3. Sélectionnez `Dépôts personnalisés`.
4. Ajoutez `https://github.com/Bigdaddy1990/jackery_solarvault` comme `Intégration`.
5. Recherchez `Jackery SolarVault` et installez-le.
6. Redémarrez Home Assistant.
7. Allez dans `Paramètres > Appareils et services > Ajouter une intégration`.
8. Sélectionnez `Jackery SolarVault`.

### Option 2 : Manuel

1. Téléchargez la dernière version (release).
2. Copiez le dossier `custom_components/jackery_solarvault` dans le répertoire `config/custom_components/` de votre Home Assistant.
3. Redémarrez Home Assistant.

---

## ⚙️ Configuration

1. Allez dans **Paramètres → Appareils et services**.
2. Cliquez sur **Ajouter une intégration** et recherchez **Jackery SolarVault**.
3. Ajoutez et configurez d’abord l’appareil dans l’application Jackery. Utilisez ensuite dans l’assistant HA le compte cloud Jackery qui l’a ajouté.

> [!WARNING]
> **Compte SolarVault :** Selon le manuel de l'application Jackery, les systèmes SolarVault ne peuvent pas être partagés ; seul leur propriétaire peut les gérer. Utilisez le compte Jackery propriétaire de votre SolarVault pour cette intégration. Un deuxième compte HA auquel partager le SolarVault n'est pas une solution prise en charge.
> **Application et Home Assistant simultanément :** Une nouvelle connexion peut remplacer la session de l'autre client. L'intégration tente de rétablir les connexions, mais ne garantit pas une utilisation simultanée stable avec l'application mobile. Le BLE, le MQTT local et les données de session en cache ne désactivent pas la connexion au cloud. Consultez [les comptes et les alternatives locales (en anglais)](./account-sessions.md) pour les sources, les limites et l'intégration MQTT officielle distincte.

### Options de Configuration

- **E-mail & Mot de passe :** Les identifiants du compte cloud Jackery propriétaire de votre SolarVault.
- **Bluetooth (BLE) :** Optionnel. Nécessite une portée Bluetooth suffisante et une clé d'appareil valide issue de la configuration cloud ou du cache.
- **MQTT Local :** Optionnel. Nécessite un firmware compatible et un courtier local configuré. Ses identifiants sont indépendants du compte cloud Jackery ; la connexion au cloud de cette intégration reste active.

---

## 💡 Automatisations & Exemples

**Notifier lorsque la batterie est faible :**

```yaml
automation:
  - alias: "Jackery Avertissement Batterie Faible"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.jackery_solarvault_state_of_charge
        below: 20
    actions:
      - action: notify.notify
        data:
          message: "La batterie de votre Jackery est inférieure à 20 % !"
```

**Définir un calendrier d'Heure d'Utilisation (Time-of-Use) :**

```yaml
action: jackery_solarvault.save_tou_plan
data:
  device_id: "<votre_device_id>"
  body:
    tasks:
      - start: "04:00"
        end: "06:00"
        loops: "1111111"
        pw: 700
        sysSwitch: 1
```

---

## ❓ Dépannage (FAQ)

- **L'application mobile me déconnecte ou l'intégration perd sa connexion après une connexion dans l'application :**
  Un conflit de sessions est possible lorsque l'application et l'intégration utilisent le même compte propriétaire. Un deuxième compte ne résout pas ce problème pour SolarVault, car le système ne peut pas être partagé. Pour alterner entre les clients, désactivez l'intégration avant de vous connecter à l'application, puis terminez la session de l'application avant de réactiver l'intégration. Des connexions ou redémarrages répétés ne créent pas une utilisation simultanée fiable. Pour les autres erreurs de connexion, vérifiez aussi le réseau, le courtier et la portée BLE. [Détails et alternative MQTT officielle (en anglais)](./account-sessions.md).
- **Où sont les capteurs d'énergie à vie ?**
  Les capteurs de semaine, mois et année fournis sont des *totales de période* et se réinitialisent automatiquement. Pour le tableau de bord Énergie de Home Assistant, veuillez utiliser les capteurs d'énergie cumulée fournis par l'intégration.

---

## 📜 Licence

Ce projet est sous licence MIT — voir le fichier [LICENSE](../LICENSE) pour plus de détails.
