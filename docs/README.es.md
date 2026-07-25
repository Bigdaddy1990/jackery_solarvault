# Jackery SolarVault — Integración para Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Una integración personalizada de [Home Assistant](https://www.home-assistant.io/) que lleva tus estaciones de energía Jackery SolarVault, HomePower y Explorer directamente a tu hogar inteligente.

Ya sea que desees monitorear tu entrada solar, verificar el nivel de tu batería o automatizar tu hogar basándote en el flujo de energía de tu Jackery, esta integración te brinda datos en tiempo real y control total sobre tus dispositivos Jackery.

---

## 🔋 Dispositivos Soportados

Esta integración es compatible con una amplia gama de dispositivos Jackery, incluyendo:

- **Sistemas de Energía para el Hogar:** Serie Jackery SolarVault y HomePower.
- **Estaciones de Energía Portátiles (Serie Explorer):** E240, E557, E900, E1000, E1500V2, E1800, E2000, E3000, E7647, E7987.
- **Accesorios:** Baterías adicionales, medidores inteligentes (Smart Meters), enchufes inteligentes y enchufes de la nube de Shelly vinculados.

---

## ✨ Características

- **Monitoreo en Vivo:** Sigue el estado de carga (SOC), potencia de entrada FV (Solar), importación/exportación de red, potencia de salida y temperaturas.
- **Control Total:** Alterna salidas de CA/CC, cambia modos de trabajo, establece límites de carga y prioriza salidas.
- **Automatizaciones:** Automatiza tu estación de energía Jackery según condiciones (por ejemplo, encender un enchufe inteligente cuando la batería alcance el 100%).
- **Panel de Energía:** Se integra perfectamente con el panel de energía de Home Assistant utilizando estadísticas a largo plazo (diarias, semanales, mensuales, anuales).
- **Rápido y Confiable:** Utiliza la Nube de Jackery como fuente principal de verdad, mejorada con Bluetooth (BLE) y MQTT locales para actualizaciones instantáneas de baja latencia.

---

## 🛠️ Instalación

### Opción 1: HACS (Recomendado)
1. Abre Home Assistant y ve a **HACS**.
2. Añade este repositorio como un repositorio personalizado (Categoría: *Integration*).
3. Busca **Jackery SolarVault** y haz clic en Descargar.
4. Reinicia Home Assistant.

### Opción 2: Manual
1. Descarga la última versión (release).
2. Copia la carpeta `custom_components/jackery_solarvault` en tu directorio `config/custom_components/` de Home Assistant.
3. Reinicia Home Assistant.

---

## ⚙️ Configuración

1. Ve a **Ajustes → Dispositivos y servicios**.
2. Haz clic en **Añadir integración** y busca **Jackery SolarVault**.
3. Sigue el asistente de configuración e introduce tus credenciales de la Nube de Jackery.

> [!WARNING]
> **Limitación Importante de la Cuenta:** Jackery solo permite una sesión activa por cuenta. Si inicias sesión con la cuenta principal de tu aplicación, cerrarás sesión regularmente en tu teléfono, o la integración se desconectará.  
> **Solución:** ¡Crea una **segunda cuenta de Jackery dedicada** solo para Home Assistant! Comparte tus dispositivos Jackery desde tu cuenta principal de la aplicación con esta nueva cuenta dedicada para HA.

### Opciones de Configuración
- **Correo Electrónico y Contraseña:** Las credenciales de tu cuenta dedicada de la Nube de Jackery.
- **Bluetooth (BLE):** Opcional. Permite la comunicación directa cuando tu servidor de HA está dentro del alcance Bluetooth de la Jackery.
- **MQTT Local:** Opcional. Usa esto si tu dispositivo está configurado para publicar datos a un bróker MQTT local.

---

## 💡 Automatizaciones y Ejemplos

**Notificar cuando la batería está baja:**
```yaml
automation:
  - alias: "Jackery Advertencia de Batería Baja"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.jackery_solarvault_state_of_charge
        below: 20
    actions:
      - action: notify.notify
        data:
          message: "¡La batería de tu Jackery está por debajo del 20%!"
```

**Configurar un horario de Tiempo de Uso (Time-of-Use):**
Puedes usar los servicios personalizados de la integración para cambiar ajustes avanzados como los horarios de carga:
```yaml
action: jackery_solarvault.save_tou_plan
data:
  device_id: "<tu_device_id>"
  body:
    tasks:
      - start: "04:00"
        end: "06:00"
        loops: "1111111"
        pw: 700
        sysSwitch: 1
```

---

## ❓ Solución de Problemas (FAQ)

- **Mi integración se desconecta continuamente o los sensores dicen "no disponible":**
  Esto suele suceder porque estás usando la misma cuenta en la App Jackery de tu teléfono y en Home Assistant. Por favor, crea una cuenta dedicada para Home Assistant y compártele tus dispositivos.
- **No veo actualizaciones en vivo, tarda unos minutos en actualizarse:**
  La API en la nube se consulta cada pocos minutos. Para actualizaciones en vivo instantáneas, activa la función opcional Bluetooth (BLE) en la configuración, siempre que tu servidor de Home Assistant esté lo suficientemente cerca del dispositivo Jackery.
- **¿Dónde están los sensores de energía acumulada de por vida?**
  Los sensores proporcionados de semana, mes y año son *totales del período* y se reinician automáticamente. Para el Panel de Energía de Home Assistant, por favor usa los sensores de energía acumulativa provistos por la integración.

---

## 📜 Licencia

Este proyecto está licenciado bajo la Licencia MIT - consulta el archivo [LICENSE](LICENSE) para más detalles.
