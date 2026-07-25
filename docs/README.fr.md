# Jackery SolarVault — Intégration Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Une intégration personnalisée pour [Home Assistant](https://www.home-assistant.io/) qui intègre vos stations d'énergie Jackery SolarVault, HomePower et Explorer directement dans votre maison intelligente.

Que vous souhaitiez surveiller votre apport solaire, vérifier le niveau de votre batterie ou automatiser votre maison en fonction du flux d'énergie de votre Jackery – cette intégration vous offre des données en temps réel et un contrôle total sur vos appareils Jackery.

---

## 🔋 Appareils Compatibles

Cette intégration prend en charge une large gamme d'appareils Jackery, notamment :

- **Systèmes d'Énergie Domestique :** Série Jackery SolarVault et HomePower.
- **Stations d'Énergie Portables (Série Explorer) :** E240, E557, E900, E1000, E1500V2, E1800, E2000, E3000, E7647, E7987.
- **Accessoires :** Batteries supplémentaires, compteurs intelligents (Smart Meters), prises intelligentes et prises cloud Shelly liées.

---

## ✨ Fonctionnalités

- **Surveillance en Direct :** Suivez l'état de charge (SOC), la puissance d'entrée PV (Solaire), l'import/export du réseau, la puissance de sortie et les températures.
- **Contrôle Total :** Basculez les sorties AC/DC, changez les modes de fonctionnement, définissez les limites de charge et priorisez les sorties.
- **Automatisations :** Automatisez facilement votre station d'énergie Jackery selon des conditions (ex. : allumer une prise intelligente lorsque la batterie atteint 100 %).
- **Tableau de Bord Énergie :** S'intègre parfaitement au tableau de bord Énergie de Home Assistant grâce aux statistiques à long terme (journalières, hebdomadaires, mensuelles, annuelles).
- **Rapide & Fiable :** Utilise le Cloud Jackery comme source de vérité principale, complétée par le Bluetooth local (BLE) et MQTT pour des mises à jour instantanées avec une faible latence.

---

## 🛠️ Installation

### Option 1 : HACS (Recommandé)
1. Ouvrez Home Assistant et allez dans **HACS**.
2. Ajoutez ce dépôt comme dépôt personnalisé (Catégorie : *Integration*).
3. Recherchez **Jackery SolarVault** et cliquez sur Télécharger.
4. Redémarrez Home Assistant.

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
Vous pouvez utiliser les services personnalisés de l'intégration pour modifier des paramètres avancés comme les plannings de charge :
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
- **Je ne vois pas les mises à jour en direct, cela prend quelques minutes :**
  L'API cloud est interrogée toutes les quelques minutes. Pour des mises à jour en direct instantanées, activez la fonction optionnelle Bluetooth (BLE) dans la configuration, à condition que votre serveur Home Assistant soit suffisamment proche de l'appareil Jackery.
- **Où sont les capteurs d'énergie à vie ?**
  Les capteurs de semaine, mois et année fournis sont des *totaux de période* et se réinitialisent automatiquement. Pour le tableau de bord Énergie de Home Assistant, veuillez utiliser les capteurs d'énergie cumulée fournis par l'intégration.

---

## 📜 Licence

Ce projet est sous licence MIT - voir le fichier [LICENSE](LICENSE) pour plus de détails.
