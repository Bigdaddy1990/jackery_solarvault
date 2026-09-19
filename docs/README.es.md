# Jackery SolarVault for Home Assistant

Languages:
[English](../README.md) · [Deutsch](./README.de.md) · [Français](./README.fr.md) · [Español](./README.es.md)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)

Una integración personalizada de [Home Assistant](https://www.home-assistant.io/) que lleva tus estaciones de energía Jackery SolarVault, HomePower y Explorer directamente a tu hogar inteligente.

**Esta es la integración definitiva (non-plus-ultra) de Jackery para Home Assistant.** Combina el 100% de la funcionalidad de la aplicación oficial (Cloud API) con la velocidad y fiabilidad de **MQTT Local** y **Bluetooth (BLE)**.

---

## 🏆 Por qué esta integración es la mejor opción

Es posible que hayas oído hablar de otras soluciones MQTT manuales o integraciones más antiguas. Aquí te explicamos por qué esta integración es claramente la opción superior:

1. **Sin Extracción Manual de Tokens:** Requerimos tus credenciales de la Nube durante la configuración. **¿Por qué?** Porque la integración descubre automáticamente todos tus dispositivos y obtiene de forma segura las complejas claves de encriptación y tokens necesarios para la comunicación local. No tienes que interceptar tráfico de red ni configurar payloads JSON manualmente.
2. **Verdadero Control Local:** Una vez completado el intercambio inicial con la nube, la integración se conecta directamente a tu dispositivo a través de **MQTT Local** y **Bluetooth (BLE)** para actualizaciones instantáneas en milisegundos y control local.
3. **100% de la Funcionalidad de la App:** A diferencia de los scripts básicos de solo lectura local que solo obtienen los niveles de batería, esta integración admite *todo* lo que hace la aplicación Jackery, incluyendo programación de Tiempo de Uso, integración con Shelly, comprobaciones de firmware y ajustes de carga avanzados.

---

## 🔋 Dispositivos Soportados

Esta integración es compatible con una amplia gama de dispositivos Jackery, incluyendo:

- **Sistemas de Energía para el Hogar:** Serie Jackery SolarVault y HomePower.
- **Estaciones de Energía Portátiles (Serie Explorer):** E240, E557, E900, E1000, E1500V2, E1800, E2000, E3000, E7647, E7987.
- **Accesorios:** Baterías adicionales, medidores inteligentes (Smart Meters), enchufes inteligentes y enchufes de la nube de Shelly vinculados.

---

## ✨ Características y Entidades

La integración crea docenas de entidades por dispositivo para brindarte visibilidad y control total:

| Plataforma | Ejemplos |
|------------|----------|
| `sensor` | SOC, potencia de entrada/salida/FV, red de entrada/salida, temperaturas, tiempo de funcionamiento restante, estadísticas de energía |
| `binary_sensor` | estado de carga, en línea y fallos |
| `number` | límite de potencia de carga, límites de almacenamiento de energía, límites de batería personalizados, retardo de salida CA |
| `select` | modo de trabajo, modo de carga, prioridad de salida, modelo SAI/UPS, modo de precio de electricidad |
| `switch` | salida EPS, salidas CA/CC, ahorro de energía, carga súper rápida |
| `button` | reiniciar, parpadeo del paquete de batería |
| `text` | identificadores de Wi-Fi y diagnóstico |

### 🛠️ Servicios Avanzados

También exponemos más de 60 servicios personalizados en Home Assistant, brindándote el poder de la App Jackery en tus automatizaciones:
- **Gestión de Dispositivos:** `bind_device`, `unbind_device`, `get_share_qr_code`
- **Cloud-to-Cloud:** `get_shelly_auth_url`, `list_shelly_devices`
- **Programación de Energía:** `save_tou_plan`, `insert_electricity_strategy`, `bind_currency`
- **Estadísticas:** `query_charge_report`, `query_soc_stat`, `query_profit_stat`

---

## 🛠️ Instalación

### HACS (Recomendado)

1. Abre HACS.
2. Abre el menú de tres puntos.
3. Selecciona `Repositorios personalizados`.
4. Añade `https://github.com/Bigdaddy1990/jackery_solarvault` como una `Integración`.
5. Busca `Jackery SolarVault` e instálalo.
6. Reinicia Home Assistant.
7. Ve a `Ajustes > Dispositivos y servicios > Añadir integración`.
8. Selecciona `Jackery SolarVault`.

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
> **Limitación Importante de la Cuenta:** Jackery normalmente permite una sesión activa por cuenta. Si inicias sesión con otro dispositivo (por ejemplo, tu aplicación principal en tu teléfono), la conexión MQTT de la integración se pausará temporalmente y se reconectará automáticamente poco después.
> **Solución Recomendada:** Para obtener la mejor experiencia, crea una **segunda cuenta de Jackery dedicada** solo para Home Assistant. Comparte tus dispositivos Jackery desde tu cuenta principal de la aplicación con esta nueva cuenta dedicada para HA.

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
- **¿Dónde están los sensores de energía acumulada de por vida?**
  Los sensores proporcionados de semana, mes y año son *totales del período* y se reinician automáticamente. Para el Panel de Energía de Home Assistant, por favor usa los sensores de energía acumulativa provistos por la integración.

---

## 📜 Licencia

Este proyecto está licenciado bajo la Licencia MIT - consulta el archivo [LICENSE](../LICENSE) para más detalles.
