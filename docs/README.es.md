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

1. **Sin extracción manual de tokens:** La configuración utiliza tus credenciales de la nube para descubrir los dispositivos y obtener las claves y los datos de sesión MQTT. No tienes que interceptar tráfico de red ni crear payloads JSON manualmente.
2. **Control local opcional:** En dispositivos compatibles, un bróker MQTT local configurado y **Bluetooth (BLE)** activado pueden transmitir datos y comandos locales. Estos canales adicionales no sustituyen el inicio de sesión en la nube ni desactivan HTTP o MQTT en la nube. Esta integración no ofrece una configuración completamente local.
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
- **Gestión de Dispositivos:** `bind_device`, `unbind_device`, `get_share_qr_code` (según el dispositivo; los servicios para compartir no demuestran que SolarVault admita compartir sistemas)
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
> **Cuenta SolarVault:** Según el manual de la aplicación Jackery, los sistemas SolarVault no se pueden compartir; solo el propietario puede gestionarlos. Utiliza la cuenta Jackery propietaria de tu SolarVault para esta integración. Una segunda cuenta de HA con un SolarVault compartido no es una solución admitida.
> **Aplicación y Home Assistant simultáneamente:** Un nuevo inicio de sesión puede sustituir la sesión del otro cliente. La integración intenta restablecer las conexiones, pero no garantiza un uso simultáneo estable con la aplicación móvil. BLE, MQTT local y los datos de sesión en caché tampoco desactivan el inicio de sesión en la nube. Consulta [las cuentas y las alternativas locales (en inglés)](./account-sessions.md) para ver las fuentes, las limitaciones y la integración MQTT oficial independiente.

### Opciones de Configuración

- **Correo Electrónico y Contraseña:** Las credenciales de la cuenta de la nube Jackery propietaria de tu SolarVault.
- **Bluetooth (BLE):** Opcional. Requiere alcance Bluetooth y una clave de dispositivo válida obtenida durante la configuración en la nube o desde la caché.
- **MQTT Local:** Opcional. Requiere firmware compatible y un bróker local configurado. Sus credenciales son independientes de la cuenta de la nube Jackery; el inicio de sesión en la nube de esta integración sigue activo.

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

- **La aplicación móvil cierra mi sesión o la integración pierde la conexión después de iniciar sesión en la aplicación:**
  Puede haber un conflicto de sesiones cuando la aplicación y la integración usan la misma cuenta propietaria. Una segunda cuenta no resuelve este problema para SolarVault porque el sistema no se puede compartir. Para alternar entre clientes, desactiva la integración antes de iniciar sesión en la aplicación y termina la sesión de la aplicación antes de reactivar la integración. Iniciar sesión o reiniciar repetidamente no permite un uso simultáneo fiable. Para otros errores de conexión, revisa también la red, el bróker y el alcance BLE. [Detalles y alternativa MQTT oficial (en inglés)](./account-sessions.md).
- **¿Dónde están los sensores de energía acumulada de por vida?**
  Los sensores proporcionados de semana, mes y año son *totales del período* y se reinician automáticamente. Para el Panel de Energía de Home Assistant, por favor usa los sensores de energía acumulativa provistos por la integración.

---

## 📜 Licencia

Este proyecto está licenciado bajo la Licencia MIT - consulta el archivo [LICENSE](../LICENSE) para más detalles.
