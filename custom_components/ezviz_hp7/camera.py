from __future__ import annotations
import logging

from homeassistant.components.camera import Camera
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    serial = data["serial"]
    async_add_entities([Hp7LastSnapshotCamera(hass, coordinator, serial)])

class Hp7LastSnapshotCamera(Camera, CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, hass, coordinator, serial: str):
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self.hass = hass
        self._serial = serial
        self._attr_name = "Ultima Istantanea"
        self._attr_unique_id = f"{DOMAIN}_{serial}_last_snapshot"

    @property
    def device_info(self) -> DeviceInfo:
        model = getattr(self.coordinator.api, "model", "HP7")
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=f"EZVIZ {model} ({self._serial})",
            manufacturer="EZVIZ",
            model=model,
        )

    async def async_camera_image(self, width: int | None = None, height: int | None = None):
        url = (self.coordinator.data or {}).get("last_alarm_pic")
        if not url:
            return None

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, timeout=15) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception:
            return None

    @property
    def supported_features(self) -> int:
        return 0

    async def _async_get_supported_webrtc_provider(self, *args, **kwargs):
        return None

    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
