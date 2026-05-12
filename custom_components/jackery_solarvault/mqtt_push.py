"""Compatibility wrapper for the internal Jackery MQTT client package."""

from .client.mqtt_push import JackeryMqttPushClient

__all__ = ["JackeryMqttPushClient"]
