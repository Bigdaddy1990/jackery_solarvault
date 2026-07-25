# Jackery SolarVault for Home Assistant

Languages:
[English](../README.md) · [Deutsch](./README.de.md) · [Français](./README.fr.md) · [Español](./README.es.md)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Une intégration personnalisée pour [Home Assistant](https://www.home-assistant.io/) qui intègre vos stations d'énergie Jackery SolarVault, HomePower et Explorer directement dans votre maison intelligente.

**Ceci est l'intégration Jackery ultime (non-plus-ultra) pour Home Assistant.** Elle combine 100 % des fonctionnalités de l'application officielle (Cloud API) avec la vitesse et la fiabilité du **MQTT local** et du **Bluetooth (BLE)**.

---

## 🏆 Pourquoi cette intégration est le meilleur choix

Vous avez peut-être entendu parler d'autres solutions MQTT manuelles ou d'intégrations plus anciennes. Voici pourquoi cette intégration est clairement supérieure :

1. **Aucune Extraction Manuelle de Tokens :** Nous nécessitons vos identifiants Cloud lors de l'installation. **Pourquoi ?** Parce que l'intégration découvre automatiquement tous vos appareils et récupère en toute sécurité les clés de cryptage et les tokens complexes requis pour la communication locale. Vous n'avez pas à intercepter le trafic réseau ni à configurer manuellement des charges utiles JSON.
2. **Vrai Contrôle Local :** Une fois la liaison cloud initiale terminée, l'intégration se connecte directement à votre appareil via **MQTT Local** et **Bluetooth (BLE)** pour des mises à jour instantanées de l'ordre de la milliseconde et un contrôle local.
3. **100 % des Fonctionnalités de l'App :** Contrairement aux scripts locaux basiques qui ne lisent que les niveaux de batterie, cette intégration prend en charge *tout* ce que fait l'application Jackery, y compris la planification de l'Heure d'Utilisation, l'intégration Shelly, les vérifications de firmware et les paramètres de charge avancés.

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
- **Gestion des Appareils :** `bind_device`, `unbind_device`, `get_share_qr_code`
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
3. Suivez l'assistant de configuration et entrez vos identifiants Cloud Jackery.

> [!WARNING]
> **Limitation Importante du Compte :** Jackery n'autorise qu'une seule session active par compte. Si vous vous connectez avec votre compte principal, vous serez régulièrement déconnecté sur votre téléphone, ou l'intégration se déconnectera.  
> **Solution :** Créez un **deuxième compte Jackery dédié** uniquement pour Home Assistant. Partagez vos appareils Jackery depuis votre compte principal vers ce nouveau compte dédié pour HA !

### Options de Configuration
- **E-mail & Mot de passe :** Les identifiants de votre compte Cloud Jackery dédié.
- **Bluetooth (BLE) :** Optionnel. Permet une communication directe lorsque votre serveur HA est à portée Bluetooth du Jackery.
- **MQTT Local :** Optionnel. Utilisez ceci si votre appareil est configuré pour publier des données vers un courtier MQTT local.

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

- **Mon intégration se déconnecte sans cesse ou les capteurs affichent "indisponible" :**
  Cela se produit généralement parce que vous utilisez le même compte dans l'application Jackery sur votre téléphone et dans Home Assistant. Veuillez créer un compte dédié pour Home Assistant et lui partager vos appareils.
- **Où sont les capteurs d'énergie à vie ?**
  Les capteurs de semaine, mois et année fournis sont des *totales de période* et se réinitialisent automatiquement. Pour le tableau de bord Énergie de Home Assistant, veuillez utiliser les capteurs d'énergie cumulée fournis par l'intégration.

---

## 📜 Licence

Ce projet est sous licence MIT - voir le fichier [LICENSE](LICENSE) pour plus de détails.
