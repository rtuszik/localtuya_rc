"""LocalTuyaIR Remote Control integration."""
import logging
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform

_LOGGER = logging.getLogger(__name__)

# The remote platform has always been supported. Home Assistant 2026.4 added a
# dedicated "infrared" entity platform that consumer integrations (e.g. LG
# Infrared) use to discover IR proxies. Register on it as well when available,
# while staying loadable on older HA versions that lack Platform.INFRARED.
PLATFORMS = [Platform.REMOTE]
if hasattr(Platform, "INFRARED"):
    PLATFORMS.append(Platform.INFRARED)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Tuya Remote Control from a config entry."""
    _LOGGER.debug("Setting up entry, platforms: %s", PLATFORMS)
    # Add the remote control entity (and the IR emitter when supported)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register update listener for options flow
    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    _LOGGER.debug("Unloading")
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

async def update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Handle options update."""
    _LOGGER.debug("Options update for %s: %s", entry.entry_id, entry.options)
    await hass.config_entries.async_reload(entry.entry_id)
