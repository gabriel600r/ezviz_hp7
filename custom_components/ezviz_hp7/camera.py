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

    # ---------------- helpers ----------------

    def _normalize_url(self, url: str | None) -> str | None:
        if not url:
            return None
        url = url.strip()
        if url.startswith("//"):
            url = f"https:{url}"
        try:
            parsed = urlparse(url)
            _LOGGER.debug("HP7 snapshot: host=%s path=%s", parsed.netloc, parsed.path)
        except Exception:
            pass
        return url

    async def _fetch_image(self, url: str | None):
        url = self._normalize_url(url)
        if not url:
            _LOGGER.debug("HP7 snapshot: sin URL para descargar")
            return None

        session = async_get_clientsession(self.hass)
        headers = {
            "User-Agent": "okhttp/4.9",
            "Accept": "*/*",
            "Referer": "https://ezvizlife.com/",
        }
        try:
            async with session.get(url, timeout=20, allow_redirects=True, headers=headers) as resp:
                ctype = resp.headers.get("Content-Type", "")
                data = await resp.read()
                if ctype.startswith("image/"):
                    _LOGGER.debug("HP7 snapshot: HTTP %s, Content-Type=%s, bytes=%d",
                                  resp.status, ctype, len(data))
                    return data
                # No es imagen: log detallado (preview texto/hex)
                preview = data[:120]
                try:
                    ptxt = preview.decode("utf-8", "ignore").replace("\n", " ")[:120]
                    _LOGGER.debug("HP7 snapshot: NO-IMAGE HTTP %s, CT=%s, bytes=%d, preview='%s'",
                                  resp.status, ctype, len(data), ptxt)
                except Exception:
                    _LOGGER.debug("HP7 snapshot: NO-IMAGE HTTP %s, CT=%s, bytes=%d (binario)",
                                  resp.status, ctype, len(data))
                return None
        except Exception as e:
            _LOGGER.debug("HP7 snapshot fetch error: %s", e)
            return None

    def _extract_url_from(self, data: dict | None) -> str | None:
        if not data:
            return None
        # Claves posibles según variantes de backend
        candidates = [
            "last_alarm_pic", "lastAlarmPic",
            "picUrl", "picURL", "cover", "deviceCover",
            "last_cover", "lastCover", "thumbnail", "thumbUrl",
        ]
        for k in candidates:
            v = data.get(k)
            if v:
                _LOGGER.debug("HP7 snapshot: usando clave %s", k)
                return v
        _LOGGER.debug("HP7 snapshot: sin URL en keys=%s", list(data.keys()))
        return None

    # ---------------- Camera API ----------------

    async def async_camera_image(self, width: int | None = None, height: int | None = None):
        # 1) Intento con el estado del coordinator
        url = self._extract_url_from(self.coordinator.data)
        img = await self._fetch_image(url)
        if img:
            return img

        # 2) Si falló, pedimos status fresco a la API
        try:
            status = await self.hass.async_add_executor_job(self.coordinator.api.get_status, self._serial)
            url2 = self._extract_url_from(status)
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
