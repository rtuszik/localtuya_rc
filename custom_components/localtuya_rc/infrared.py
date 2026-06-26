"""Infrared emitter platform for the LocalTuyaIR Remote Control integration.

Home Assistant 2026.4 introduced the ``infrared`` entity platform as a
standardised abstraction layer for IR transmitters. Consumer integrations such
as LG Infrared discover available IR proxies exclusively through this platform
and have no awareness of ``remote`` entities.

This module registers the very same physical Tuya Wi-Fi IR blaster (already
exposed as a ``remote`` entity in :mod:`.remote`) as an ``infrared`` emitter so
that it becomes discoverable by those consumers. The existing remote platform
and the underlying Tuya communication code are left untouched: this entity
reuses the proven :class:`~.remote.TuyaRC` class purely as a communication
driver.
"""
import asyncio
import logging
from typing import override

from homeassistant.components.infrared import InfraredCommand, InfraredEmitterEntity
from homeassistant.const import (
    CONF_NAME,
    CONF_HOST,
    CONF_DEVICE_ID,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    DEFAULT_FRIENDLY_NAME,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL_VERSION,
    CONF_CONTROL_TYPE,
    CONF_CLOUD_INFO,
)
from .remote import TuyaRC

_LOGGER = logging.getLogger(__name__)

# Tuya needs a low (space) timing after the final high pulse, and the device
# cannot represent a single gap longer than this in one packet. Codes with a
# longer gap (e.g. inter-frame pauses on AC remotes) are split into separate
# transmissions with a real-time sleep in between, mirroring the behaviour of
# tinytuya-based senders.
_MAX_TUYA_GAP = 50000
_GAP_MARKER = 5000


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Tuya IR emitter entity for a config entry."""
    config = entry.data
    if config is None:
        _LOGGER.error("Configuration is empty")
        return

    name = config.get(CONF_NAME, DEFAULT_FRIENDLY_NAME)
    dev_id = config.get(CONF_DEVICE_ID)
    host = config.get(CONF_HOST)
    local_key = config.get(CONF_LOCAL_KEY)
    protocol_version = config.get(CONF_PROTOCOL_VERSION)
    cloud_info = config.get(CONF_CLOUD_INFO, None)
    control_type = config.get(CONF_CONTROL_TYPE, 0)

    if name is None or host is None or dev_id is None or local_key is None:
        _LOGGER.error("Missing required configuration items")
        return

    _LOGGER.debug("Setting up Tuya IR emitter: name=%s, dev_id=%s, host=%s", name, dev_id, host)

    # The emitter is a write-only, non-polling entity, so it never needs to
    # hold a persistent socket of its own. Forcing a non-persistent driver
    # avoids competing with the remote entity for the device's single local
    # connection slot.
    driver = TuyaRC(
        name,
        dev_id,
        host,
        local_key,
        protocol_version,
        persistent_connection=False,
        cloud_info=cloud_info,
        control_type=control_type,
        entry=entry,
    )

    async_add_entities([TuyaInfraredEmitter(driver, name, dev_id, cloud_info)])


class TuyaInfraredEmitter(InfraredEmitterEntity):
    """Exposes a Tuya Wi-Fi IR blaster on the HA ``infrared`` platform."""

    _attr_has_entity_name = True
    _attr_name = "IR emitter"

    def __init__(self, driver: TuyaRC, name, dev_id, cloud_info):
        self._driver = driver
        self._device_name = name
        self._dev_id = dev_id
        self._cloud_info = cloud_info
        # Emitters are write-only; report available until a send proves
        # otherwise. This also avoids a startup connection that would race the
        # remote entity's availability probe for the same device.
        self._attr_available = True

    @property
    def unique_id(self):
        return f"{self._dev_id}_infrared"

    @property
    def device_info(self):
        # Same identifiers as the remote entity so both attach to the one
        # physical device in the device registry.
        return DeviceInfo(
            name=self._device_name,
            manufacturer="Tuya",
            identifiers={(DOMAIN, self._dev_id)},
            connections={(DOMAIN, self._cloud_info['mac'])} if self._cloud_info and 'mac' in self._cloud_info else set(),
            model=self._cloud_info['model'] if self._cloud_info and 'model' in self._cloud_info else None,
            serial_number=self._cloud_info['sn'] if self._cloud_info and 'sn' in self._cloud_info else None,
        )

    async def async_will_remove_from_hass(self):
        _LOGGER.debug("Removing IR emitter %s from Home Assistant...", self._dev_id)
        await super().async_will_remove_from_hass()
        await self.hass.async_add_executor_job(self._driver._deinit)

    @override
    async def async_send_command(self, command: InfraredCommand) -> None:
        """Send an IR command received from an infrared consumer.

        The infrared platform provides signed microsecond timings (positive =
        pulse/high, negative = space/low). Tuya expects unsigned absolute
        durations, alternating high/low, with the trailing low present.
        """
        timings = command.get_raw_timings()

        # Build the unsigned pulse list, recording where over-long gaps must be
        # broken into separate transmissions.
        split = {}
        raw = []
        for i, timing in enumerate(timings):
            utiming = abs(timing)
            if utiming > _MAX_TUYA_GAP:
                split[i] = utiming - _GAP_MARKER
                raw.append(_GAP_MARKER)
            else:
                raw.append(utiming)

        # HA omits the final trailing low timing; Tuya needs it.
        if len(raw) % 2 == 1:
            raw.append(_GAP_MARKER)

        try:
            start = 0
            for index, gap in split.items():
                await self.hass.async_add_executor_job(self._driver._send_button, raw[start:index])
                start = index
                await asyncio.sleep(gap / 1000000.0)
            if start < len(raw):
                await self.hass.async_add_executor_job(self._driver._send_button, raw[start:])
        except HomeAssistantError:
            self._attr_available = False
            raise
        except Exception as e:
            self._attr_available = False
            _LOGGER.error("Failed to send IR command, exception %s: %s", type(e), e, exc_info=True)
            raise HomeAssistantError(str(e))
        else:
            self._attr_available = True
