# Integración de Home Assistant para Jackery SolarVault 3 Pro Max

**🌍 Language / Sprache / Idioma / Langue:**
[🇬🇧 English](../README.md) · [🇩🇪 Deutsch](README.de.md) · [🇫🇷 Français](README.fr.md) · [🇪🇸 Español](README.es.md)

---

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)
[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)

Integración comunitaria para sistemas Jackery SolarVault, especialmente SolarVault 3 Pro Max. La integración lee valores en directo, estadísticas de energía y parámetros configurables desde la nube de Jackery, y usa MQTT push para cambios de estado rápidos y comandos de control.

> ⚠️ Esta integración no es un producto oficial de Jackery y no está afiliada a Jackery Inc.


## Funciones

- Detección automática de dispositivos y del sistema mediante la cuenta de Jackery
- Actualización HTTP periódica de los valores estándar con un intervalo fijo de 30 segundos
- MQTT push para estado en directo, smart meter, baterías de expansión y comandos de control
- Unidad principal, smart meter y baterías de expansión como dispositivos separados de Home Assistant
- Compatibilidad con hasta 5 baterías de expansión
- Potencia en directo: batería, PV total, canales PV, importación de red, exportación a red, EPS y pila de baterías de expansión
- Estadísticas de energía: día, semana, mes y año para PV, consumo y batería
- Valores a largo plazo aptos para el panel de Energía solo para valores acumulativos totales/diarios; los valores semanales/mensuales/anuales son solo valores de visualización
- Potencia del smart meter incluyendo valores por fase, si hay un smart meter conectado
- Configuración mediante entidades: EPS, límites de carga/descarga, límite de potencia de inyección, potencia máxima de salida, modo de consumo energético, autoapagado, seguimiento del smart meter, aviso de tormenta, unidad de temperatura, precio de electricidad y standby
- Botón de reinicio para el dispositivo
- Entidades de diagnóstico para estado en línea, firmware, límites del sistema, estándar de red, código de país, datos sin procesar y estado MQTT

## Instalación mediante HACS

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)

1. Abrir HACS.
2. Abrir el menú de tres puntos en la parte superior derecha.
3. Seleccionar `Custom repositories`.
4. Introducir la URL del repositorio `https://github.com/Bigdaddy1990/jackery_solarvault` y elegir la categoría `Integration`.
5. Buscar `Jackery SolarVault` e instalarlo.
6. Reiniciar Home Assistant.
7. Ir a Ajustes → Dispositivos y servicios → Añadir integración → `Jackery SolarVault`.

## Instalación manual

1. Descargar el ZIP desde la [página de releases](https://github.com/Bigdaddy1990/jackery_solarvault/releases).
2. Copiar la carpeta `custom_components/jackery_solarvault` a `<HA-config>/custom_components/`.
3. Reiniciar Home Assistant.
4. Añadir la integración desde Ajustes → Dispositivos y servicios.

## Configuración

Requisitos:

- correo electrónico de la nube de Jackery
- contraseña de la nube de Jackery
- opcional: activar/desactivar sensores calculados del smart meter
- opcional: activar/desactivar sensores de potencia calculados

El ID del dispositivo, el ID del sistema, la macId de MQTT y la región se derivan de los datos de la nube/MQTT y ya no se solicitan manualmente en la interfaz de usuario.

## Nota importante sobre el inicio de sesión de Jackery

Jackery permite en la práctica solo una sesión activa por cuenta. Si la app oficial y Home Assistant están conectados al mismo tiempo con la misma cuenta, los tokens y las credenciales MQTT pueden rotar. Esto puede provocar tokens caducados o errores de autenticación MQTT.

Recomendado:

1. Crear una segunda cuenta de Jackery.
2. Compartir el SolarVault con la segunda cuenta en la app de Jackery mediante compartir/código QR.
3. Usar la segunda cuenta en Home Assistant.

## Entidades

### Sensores normales

- SOC total y batería interna
- Potencia de carga de batería y potencia de descarga de batería
- Potencia PV total y canales PV 1-4
- Importación de red, exportación a red y potencia neta de red
- Potencia de entrada/salida del lado de red
- Potencia EPS
- Potencia de carga/descarga de baterías de expansión
- Otra potencia de carga
- Precio de electricidad
- Alarmas activas
- Valores diarios/semanales/mensuales/anuales para PV, consumo y batería

### Baterías de expansión

Las baterías de expansión se crean por separado de la unidad principal. Para cada batería detectada, se muestra lo siguiente cuando está disponible:

- SOC
- Temperatura de celdas
- Potencia de carga
- Potencia de descarga
- Versión de firmware
- Estado de comunicación como atributos

### Smart meter

El smart meter se crea como dispositivo propio bajo el SolarVault. Se admiten:

- Potencia total
- Potencia de fase 1
- Potencia de fase 2
- Potencia de fase 3
- Valores sin procesar disponibles como atributos

### Entidades configurables

- Salida EPS
- Standby
- Autoapagado en modo aislado (con tiempo de autoapagado)
- Límite de carga y descarga
- Límite de potencia de inyección
- Potencia máxima de salida
- Potencia de salida predeterminada
- Seguir smart meter
- Modo de consumo energético
- Modo de precio de electricidad
- Precio de tarifa única
- Unidad de temperatura
- Aviso de tormenta y tiempo de preaviso
- Reinicio

## Servicios

La integración registra tres servicios en el espacio de nombres `jackery_solarvault`:

| Servicio | Finalidad |
|---|---|
| `jackery_solarvault.rename_system` | Renombrar el sistema (dispositivo SolarVault) en la nube |
| `jackery_solarvault.refresh_weather_plan` | Obtener el plan actual de aviso de tormenta desde el servidor en la nube |
| `jackery_solarvault.delete_storm_alert` | Eliminar una alarma de tormenta activa mediante comando en la nube |

Para más detalles sobre los parámetros necesarios, consulta `services.yaml` o el editor de Servicios en las herramientas de desarrollo de HA.

## Cómo leer correctamente los sensores de energía y potencia

- La potencia de descarga de batería muestra lo que entrega la batería.
- La red neta es la importación de red menos la exportación a red. Este valor no tiene por qué coincidir con la potencia de descarga de la batería, porque entre medias están la carga de la casa, el PV, el smart meter y la regulación interna.
- La entrada/salida de la pila se refiere a la pila de baterías de expansión o al flujo de potencia entre la unidad principal y las baterías de expansión.
- Los valores del smart meter proceden del medidor conectado y se gestionan por separado de los valores de la unidad principal.
- El sensor `Consumo actual de la casa` calcula el consumo instantáneo a partir del consumo doméstico en directo informado por Jackery (`otherLoadPw`) y solo usa como fallback la potencia neta del smart meter menos la entrada del lado de red de Jackery más la salida del lado de red de Jackery. Así se evita que la inyección del SolarVault se reste incorrectamente del consumo de la casa.
- Los sensores de energía diarios/semanales/mensuales/anuales usan `state_class: total` con el `last_reset` adecuado para el periodo correspondiente de la app. Son valores de periodo, no contadores de vida útil que aumenten de forma monótona.
- Los valores semanales, mensuales y anuales se calculan de forma idéntica a partir de la serie de gráfico correspondiente de la app. La serie depende del payload: los totales de tendencia PV/hogar suelen usar `y`, la carga/descarga de batería usa `y1`/`y2`, la entrada/salida del lado de red del dispositivo usa `y1`/`y2`, y PV1..PV4 usa `y1`..`y4`. Los campos de total del servidor ahora solo se usan como fallback/diagnóstico, porque los campos de total mensuales/anuales pueden ser engañosos según el payload.

### Periodos, totales y advertencias

- Semana = lunes a domingo.
- Mes = mes natural.
- Año = año natural.
- Los valores totales/lifetime proceden de los campos totales documentados de la app/HTTP/MQTT y no se componen a partir de valores semanales, mensuales o anuales.
- De forma explícita, no se usan valores semanales para reparar valores mensuales, anuales o totales, ni valores mensuales para reparar valores anuales o totales.
- Al inicio de un mes, el valor semanal puede ser mayor que el valor mensual si la semana actual todavía incluye días del mes anterior. Eso no es un error.
- Si Jackery entrega datos contradictorios, por ejemplo un valor anual menor que una semana completa dentro del mismo año o una producción total menor que la producción anual, la integración no cambia valores de entidades en secreto. En su lugar, crea una notificación de reparación y guarda los detalles en la exportación de diagnóstico bajo `data_quality`.

## Polling y actualización

El polling HTTP rápido se ejecuta con un intervalo fijo de 30 segundos. Las estadísticas lentas de la nube se consultan deliberadamente con menor frecuencia, porque Jackery no actualiza esos datos del lado del servidor cada segundo.

MQTT push actualiza los valores en directo independientemente del polling en cuanto el broker está conectado.

La conexión TLS de MQTT verifica activamente la cadena de certificados del broker. Se incluye ``custom_components/jackery_solarvault/jackery_ca.crt`` como ancla de confianza documentada para ``emqx.jackeryapp.com``, porque Jackery no hace firmar el broker por una CA pública. En Python 3.10+/OpenSSL 3.x, además se desactiva de forma específica el flag estricto ``VERIFY_X509_STRICT`` porque el certificado del servidor no incluye la extensión ``Authority Key Identifier``. La comprobación del nombre de host, la comprobación de cadena y la comprobación de firma permanecen activas (``CERT_REQUIRED`` + ``check_hostname = True``). No hay fallback automático a ``tls_insecure`` ni a ``CERT_NONE`` — los errores TLS siguen siendo visibles. La exportación de diagnóstico muestra bajo ``mqtt_status``, entre otros, ``tls_custom_ca_loaded``, ``tls_x509_strict_disabled`` y ``tls_certificate_source``, de modo que la configuración TLS puede revisarse sin activar el registro de depuración. El contexto y las reglas de cambio para esta estrategia están documentados en ``docs/STRICT_WORK_INSTRUCTIONS.md``.

Los datos de diagnóstico MQTT contienen únicamente rutas de topic redactadas (`hb/app/**REDACTED**/...`), contadores y marcas de tiempo de conexión, último mensaje, última publicación y payloads descartados. La parte `userId` de Jackery del topic no se incluye en la exportación de diagnóstico.

## Registro de depuración

Para el análisis de errores:

```yaml
logger:
  default: info
  logs:
    custom_components.jackery_solarvault: debug
```

## Requisitos

- Home Assistant 2025.8.0 o más reciente
- Python 3.13+ (proporcionado por Home Assistant)
- Cuenta de la nube de Jackery
- SolarVault en línea mediante Wi-Fi o Ethernet
- HACS para la instalación recomendada

## Contribuir

Envía informes de errores y solicitudes de funciones mediante [GitHub Issues](https://github.com/Bigdaddy1990/jackery_solarvault/issues). En caso de problemas de autenticación o MQTT, es muy útil una exportación de diagnóstico desde HA (Ajustes → Dispositivos y servicios → Jackery SolarVault → tres puntos → Descargar diagnóstico). Los campos sensibles se redactan automáticamente; aun así, revisa brevemente cualquier exportación de diagnóstico antes de compartirla.

## Licencia

Licencia MIT. Ver [LICENSE](../LICENSE).
