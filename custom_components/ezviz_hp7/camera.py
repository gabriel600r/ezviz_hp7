from __future__ import annotations
import logging
from urllib.parse import urlparse

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

    def _normalize_url(self, url: str | None) -> str | None:
        if not url:
            return None
        url = url.strip()
        # A veces el backend devuelve //host/path
        if url.startswith("//"):
            url = f"https:{url}"
        # Log mínimo sin query string por seguridad
        try:
            parsed = urlparse(url)
            _LOGGER.debug("HP7 snapshot host=%s path=%s", parsed.netloc, parsed.path)
        except Exception:
            pass
        return url

    async def _fetch_image(self, url: str | None):
        url = self._normalize_url(url)
        if not url:
            return None

        session = async_get_clientsession(self.hass)
        headers = {
            # headers típicos de la app móvil
            "User-Agent": "okhttp/4.9",
            "Accept": "image/*",
            "Referer": "https://ezvizlife.com/",
        }
        try:
            async with session.get(url, timeout=15, allow_redirects=True, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.read()
                _LOGGER.debug("HP7 snapshot HTTP %s para %s", resp.status, url)
        except Exception as e:
            _LOGGER.debug("HP7 snapshot fetch error: %s", e)
        return None

    async def async_camera_image(self, width: int | None = None, height: int | None = None):
        # 1) Primero intentamos con lo que tenga el coordinator
        url = (self.coordinator.data or {}).get("last_alarm_pic")
        img = await self._fetch_image(url)
        if img:
            return img

        # 2) Si falló o estaba vacío, refrescamos status desde la API
        try:
            data = await self.hass.async_add_executor_job(self.coordinator.api.get_status, self._serial)
            url2 = (data or {}).get("last_alarm_pic") or (data or {}).get("lastAlarmPic")
            img = await self._fetch_image(url2)
            if img:
                return img
        except Exception as e:
            _LOGGER.debug("HP7 snapshot refresh status error: %s", e)

        return None

    @property
    def supported_features(self) -> int:
        return 0

    async def _async_get_supported_webrtc_provider(self, *args, **kwargs):
        return None

    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
