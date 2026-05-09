# Jackery SolarVault para Home Assistant

Idiomas:
[English](../README.md) · [Deutsch](./README.de.md) · [Français](./README.fr.md) · [Español](./README.es.md)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)

Integración comunitaria para sistemas Jackery SolarVault, especialmente SolarVault 3 Pro Max. Lee valores en directo, estadísticas de energía y ajustes configurables desde la nube de Jackery, y usa MQTT push para actualizaciones rápidas y comandos de control.

Esta integración no es un producto oficial de Jackery y no está afiliada a Jackery Inc.

## Qué proporciona

- Detección automática del sistema y de dispositivos mediante la cuenta de la nube de Jackery.
- Unidad principal, smart meter y baterías de expansión como dispositivos separados de Home Assistant.
- Sensores de potencia en directo para batería, PV total, canales PV, importación/exportación de red, EPS, potencia de pila y fases del smart meter.
- Sensores de energía para periodos de la app de Jackery: día, semana, mes y año.
- Entidades configurables para EPS, standby, límites, potencia de salida, seguimiento del smart meter, aviso de tormenta, unidad de temperatura y precio de electricidad.
- Botón de reinicio del dispositivo y servicios en la nube para el nombre del sistema y la gestión de avisos de tormenta.
- Diagnósticos para datos brutos redactados, estado MQTT, firmware, límites del sistema y advertencias de calidad de datos.

## Requisitos

- Home Assistant 2025.8.0 o más reciente.
- Python 3.14 o más reciente, proporcionado por Home Assistant.
- Una cuenta de la nube de Jackery.
- SolarVault en línea mediante Wi-Fi o Ethernet.
- HACS para el método de instalación recomendado.

## Configuración recomendada de la cuenta Jackery

Jackery permite en la práctica solo una sesión activa por cuenta. Si la app oficial de Jackery y Home Assistant usan la misma cuenta al mismo tiempo, los tokens y las credenciales MQTT pueden rotar. Esto puede causar errores de token caducado, errores de autenticación MQTT o datos temporalmente obsoletos.

Configuración recomendada:

1. Crear una segunda cuenta de Jackery.
2. Compartir el SolarVault con esa segunda cuenta en la app de Jackery.
3. Usar esa segunda cuenta solo para Home Assistant.

## Instalación

### HACS

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)

1. Abrir HACS.
2. Abrir el menú de tres puntos.
3. Seleccionar `Custom repositories`.
4. Añadir `https://github.com/Bigdaddy1990/jackery_solarvault` como `Integration`.
5. Buscar `Jackery SolarVault` e instalarlo.
6. Reiniciar Home Assistant.
7. Ir a `Ajustes > Dispositivos y servicios > Añadir integración`.
8. Seleccionar `Jackery SolarVault`.

### Manual

1. Descargar el ZIP desde la [página de releases](https://github.com/Bigdaddy1990/jackery_solarvault/releases).
2. Copiar `custom_components/jackery_solarvault` en `<HA-config>/custom_components/`.
3. Reiniciar Home Assistant.
4. Añadir `Jackery SolarVault` desde `Ajustes > Dispositivos y servicios`.

## Configuración y opciones

El flujo de configuración solicita:

- Correo electrónico de la nube de Jackery.
- Contraseña de la nube de Jackery.
- Si deben crearse sensores calculados del smart meter.
- Si deben crearse sensores calculados de potencia neta.
- Si deben crearse sensores de detalle del cálculo de ahorros.

El ID del dispositivo, el ID del sistema, el `macId` MQTT y la región se derivan de los datos de la nube y MQTT. No se introducen manualmente.

Las mismas opciones pueden cambiarse más tarde desde las opciones de la integración. Las credenciales pueden actualizarse mediante los flujos de reconfiguración o reautenticación de Home Assistant sin eliminar la integración.

## Dispositivos y entidades

### Dispositivo SolarVault principal

Sensores típicos:

- Estado de carga.
- Potencia de carga y descarga de la batería.
- Potencia PV total y potencia PV1 a PV4.
- Importación de red, exportación a red y potencia neta de red.
- Potencia de entrada y salida del lado de red.
- Potencia EPS.
- Potencia de carga y descarga de la pila.
- Otra potencia de carga.
- Precio de electricidad.
- Valores de app día/semana/mes/año.
- Número de alarmas activas.

Controles típicos:

- Salida EPS.
- Standby.
- Autoapagado en modo aislado y tiempo de autoapagado.
- Límites de carga y descarga.
- Límite de potencia de inyección.
- Potencia máxima de salida.
- Potencia de salida predeterminada.
- Seguir smart meter.
- Modo de consumo energético.
- Modo de precio y precio de tarifa plana.
- Unidad de temperatura.
- Aviso de tormenta y tiempo de preaviso.
- Reinicio.

### Baterías de expansión

Las baterías de expansión se crean como dispositivos separados cuando Jackery proporciona sus datos. Se admiten hasta cinco baterías. Según el payload, cada batería puede exponer:

- Estado de carga.
- Temperatura de celdas.
- Potencia de carga y descarga.
- Versión de firmware.
- Número de serie.
- Estado de comunicación como atributos.

### Smart meter

Cuando hay un smart meter Jackery conectado, se crea como dispositivo propio. Puede exponer:

- Potencia total del medidor.
- Potencia de fase 1, fase 2 y fase 3.
- Atributos brutos del medidor para diagnóstico.
- Sensores calculados de consumo del hogar cuando la opción está activada.

## Servicios

La integración registra estos servicios bajo `jackery_solarvault`:

| Servicio | Finalidad |
|---|---|
| `jackery_solarvault.rename_system` | Renombrar el sistema SolarVault en la nube de Jackery |
| `jackery_solarvault.refresh_weather_plan` | Obtener el plan actual de aviso de tormenta |
| `jackery_solarvault.delete_storm_alert` | Eliminar un aviso de tormenta activo mediante un comando en la nube |

Usa `Herramientas para desarrolladores > Acciones` en Home Assistant para los parámetros de los servicios. Las acciones `refresh_weather_plan` y `delete_storm_alert` muestran un selector de dispositivo filtrado a dispositivos Jackery: elige la unidad principal SolarVault. Las automatizaciones también pueden pasar directamente el `device_id` numérico bruto de Jackery, visible en la exportación de diagnóstico. `rename_system` mantiene una entrada de texto porque un sistema Jackery abarca varios dispositivos de Home Assistant y se identifica mediante el ID numérico del sistema en los diagnósticos.

Cuando hay dos cuentas Jackery configuradas, cada acción se enruta automáticamente a la entrada de nube que posee el ID de sistema o dispositivo solicitado.

## Panel de Energía y significado de sensores

Usa los sensores de energía con cuidado. Jackery expone varios valores que suenan parecidos pero tienen significados diferentes.

- La potencia de descarga de la batería muestra lo que entrega la batería.
- La potencia neta de red es la importación de red menos la exportación a red. No tiene por qué coincidir con la potencia de descarga de la batería porque entre medias están el PV, la carga del hogar, los valores del smart meter y la regulación interna.
- La entrada/salida de la pila describe la pila de baterías de expansión o el flujo de potencia entre la unidad principal y las baterías de expansión.
- Los valores del smart meter proceden del medidor conectado y se tratan por separado de los valores de la unidad principal.
- `Consumo actual del hogar` usa el valor de carga doméstica en directo de Jackery (`otherLoadPw`) cuando está disponible. Si falta ese valor, la integración usa como fallback la potencia neta del smart meter menos la entrada del lado de red de Jackery más la salida del lado de red de Jackery.
- `Salida diaria a red (nube Jackery)` es el campo Jackery `todayLoad`. No es fiable como consumo real del hogar. Para el consumo del hogar, usa los sensores calculados de smart meter/consumo del hogar cuando estén disponibles.
- `Ahorro total de la app` es el KPI bruto de la app de Jackery. Puede parecer ingresos PV. `Ahorro calculado` es la estimación local basada en energía AC autoconsumida, entrada/salida del lado de red, exportación pública opcional, consumo del hogar y el precio de electricidad configurado.

Para configurar el Panel de Energía de Home Assistant, prefiere valores acumulativos/diarios reales y los sensores calculados de consumo del hogar. No trates los sensores de periodo semana, mes o año como contadores de servicio de por vida.

Los detalles del cálculo de ahorros están documentados en [`APP_CLOUD_VALUES.md`](APP_CLOUD_VALUES.md).

## Reglas de periodo y calidad de datos

La integración usa los mismos límites de periodo locales que la app de Jackery:

- Semana: lunes a domingo.
- Mes: mes natural.
- Año: año natural.

Comportamiento importante:

- Los sensores de periodo son totales de periodo, no contadores de por vida.
- Los valores semanales no se usan para reparar valores mensuales, anuales o totales.
- Cuando Jackery devuelve un valor del mes actual como valor anual o total de generación/carbono, la integración puede protegerlo hacia arriba con valores mensuales explícitos del mismo endpoint y del mismo año natural.
- `Ahorro total de la app` permanece como valor bruto de la nube. El valor de ahorro calculado es independiente.
- Al inicio de un mes, un valor semanal puede ser mayor que el valor mensual si la semana actual incluye días del mes anterior. Eso es esperado.
- Si Jackery devuelve datos contradictorios que no pueden protegerse de forma segura, la integración crea una incidencia de reparación de Home Assistant y guarda los detalles en la exportación de diagnóstico bajo `data_quality`.

## Polling, MQTT y TLS

MQTT push es la ruta principal de actualización en directo cuando está conectado. El polling HTTP sigue siendo la ruta de inicio, fallback y keep-alive:

- La actualización HTTP rápida usa un intervalo base de 30 segundos.
- Cuando MQTT está activo, los ciclos HTTP rápidos se omiten y se conserva una actualización HTTP completa con una cadencia de keep-alive más lenta.
- Las estadísticas lentas de la nube y los datos de precio/configuración se consultan con menor frecuencia porque la nube de Jackery no los actualiza cada segundo.

La conexión TLS de MQTT verifica la cadena de certificados del broker y el nombre de host. La integración incluye `custom_components/jackery_solarvault/jackery_ca.crt` como ancla de confianza para `emqx.jackeryapp.com`, porque el certificado del broker de Jackery no está firmado por una CA pública. No hay fallback automático a TLS inseguro. El estado TLS es visible en la exportación de diagnóstico.

Los detalles de implementación del manejo TLS están documentados en [`STRICT_WORK_INSTRUCTIONS.md`](STRICT_WORK_INSTRUCTIONS.md).

## Diagnóstico y solución de problemas

Para problemas de autenticación o MQTT, descarga los diagnósticos desde:

`Ajustes > Dispositivos y servicios > Jackery SolarVault > menú de tres puntos > Descargar diagnóstico`

Los campos sensibles se redactan. Las rutas de topics MQTT se exportan como `hb/app/**REDACTED**/...`; el ID de usuario Jackery bruto no se incluye. La exportación de diagnóstico también contiene contadores de payloads descartados, marcas de tiempo de conexión MQTT y advertencias de calidad de datos.

Activa el registro debug normal al investigar un problema:

```yaml
logger:
  default: info
  logs:
    custom_components.jackery_solarvault: debug
```

El registro debug de payloads HTTP/MQTT brutos está separado y es intencionadamente opcional. Solo escribe `/config/jackery_solarvault_payload_debug.jsonl` cuando este logger dedicado está en `debug`:

```yaml
logger:
  logs:
    custom_components.jackery_solarvault.payload_debug: debug
```

El archivo de debug de payloads se limita y rota a `jackery_solarvault_payload_debug.jsonl.1` al llegar a 2 MB. En instalaciones normales no existe.

Los iconos de marca de Home Assistant se cargan desde la caché de marca local `/homeassistant/.cache/brands/integrations/jackery/` cuando está disponible.

## Documentación de referencia

- [`APP_CLOUD_VALUES.md`](APP_CLOUD_VALUES.md): valores de la app/nube de Jackery y cálculo de ahorros.
- [`DATA_SOURCE_PRIORITY.md`](DATA_SOURCE_PRIORITY.md): prioridad de fuentes MQTT, HTTP y estadísticas de la app.
- [`MQTT_PROTOCOL.md`](MQTT_PROTOCOL.md): topics MQTT y contratos de payload.
- [`APP_POLLING_MQTT.md`](APP_POLLING_MQTT.md): detalles de polling HTTP y MQTT.

## Contribuir

Envía informes de errores y solicitudes de funciones mediante [GitHub Issues](https://github.com/Bigdaddy1990/jackery_solarvault/issues). Para problemas de autenticación, MQTT o calidad de datos, adjunta una exportación de diagnóstico de Home Assistant cuando sea posible. Los campos sensibles se redactan automáticamente, pero revisa igualmente el archivo antes de compartirlo públicamente.

## Licencia

Licencia MIT. Ver [LICENSE](../LICENSE).
