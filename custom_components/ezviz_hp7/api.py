from __future__ import annotations
import json
import logging
import shutil
import subprocess
from typing import Any, Dict, Optional, List

from .pylocalapi.client import EzvizClient

_LOGGER = logging.getLogger(__name__)

DEFAULT_DOOR_LOCK_NO = 2   # PORTA=2
DEFAULT_GATE_LOCK_NO = 1   # CANCELLO=1


class Hp7Api:
    def __init__(self, username: str, password: str, region: str):
        self._username = username
        self._password = password

        # Región o host. Para SA/BR probamos hosts de Brasil (sin esquema).
        reg = (region or "").strip().lower()
        self._region_or_url = reg  # valor por defecto (eu/us/cn/as)
        self._sa_hosts: List[str] = []

        if reg in ("sa", "br"):
            # Orden de prueba observado en campo (Brasil).
            self._sa_hosts = ["sadevapi.ezvizlife.com", "litedev.ezvizlife.com"]
            # Usar el primero por defecto; si falla, ensure_client() probará el siguiente.
            self._region_or_url = self._sa_hosts[0]
        elif "." in (region or ""):
            # Permitimos FQDN: pasar host "tal cual" (sin https:// para el SDK)
            self._region_or_url = region

        self._client: Optional[EzvizClient] = None
        self._user_id: Optional[str] = None
        self._cli = shutil.which("pyezvizapi")

        self.supports_door = True
        self.supports_gate = True

    # -------------------- Sessione SDK (solo per unlock) --------------------

    def _sdk_login(self, url_or_region: str) -> EzvizClient:
        """Intento de login SDK con un url/region; propaga excepción si falla."""
        _LOGGER.debug("EZVIZ HP7: intentando login SDK con '%s'", url_or_region)
        client = EzvizClient(
            account=self._username,
            password=self._password,
            url=url_or_region,
        )
        client.login()
        _LOGGER.info("EZVIZ HP7: login OK en '%s'", client._token.get("api_url", url_or_region))
        return client

    def ensure_client(self) -> None:
        if self._client is not None:
            return
        # Si hay hosts SA, probamos en orden; si no, un único intento con self._region_or_url.
        candidates = self._sa_hosts or [self._region_or_url]
        last_err: Optional[Exception] = None
        for cand in candidates:
            try:
                self._client = self._sdk_login(cand)
                # Fijamos el host efectivo para que la CLI use el mismo backend.
                self._region_or_url = cand
                return
            except Exception as e:
                _LOGGER.warning("EZVIZ HP7: login FAILED en '%s' -> %s", cand, e)
                last_err = e
        # Si llegamos acá es que fallaron todos los candidatos.
        raise last_err if last_err else RuntimeError("EZVIZ HP7: login desconocido")

    def login(self) -> bool:
        """Compat per il setup: inizializza il client SDK."""
        self.ensure_client()
        return True

    def detect_capabilities(self, serial: str) -> None:
        """Compat per il setup: per ora li consideriamo supportati."""
        try:
            self.ensure_client()
            dev = self._client.get_device_infos(serial)
            cat = dev.get("deviceInfos", {}).get("deviceCategory")
            sub = dev.get("deviceInfos", {}).get("deviceSubCategory")
            _LOGGER.info("EZVIZ HP7: device %s category=%s sub=%s", serial, cat, sub)
        except Exception as e:
            _LOGGER.debug("detect_capabilities get_device_infos fallita: %s", e)
        self.supports_door = True
        self.supports_gate = True

    def _ensure_user_id(self) -> str:
        if self._user_id:
            return self._user_id
        self.ensure_client()
        info = self._client.get_user_id()
        for k in ("userId", "username", "userName", "uid"):
            if isinstance(info, dict) and info.get(k):
                self._user_id = str(info[k])
                break
        if not self._user_id:
            self._user_id = self._username
            _LOGGER.warning("user_id non trovato; uso username come fallback.")
        return self._user_id

    # -------------------- CLI helper (status/sensori) --------------------

    def _run_cli(self, args: list[str]) -> tuple[bool, str]:
        if not self._cli:
            _LOGGER.error("CLI 'pyezvizapi' non trovata nel PATH del container.")
            return False, ""
        # Pasamos exactamente lo mismo que usa el SDK (host o region corta).
        cmd = [self._cli, "-u", self._username, "-p", self._password, "-r", self._region_or_url] + args
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=30)
            text = out.decode("utf-8", "ignore")
            return True, text
        except subprocess.CalledProcessError as e:
            text = (e.output or b"").decode("utf-8", "ignore")
            _LOGGER.error("CLI error (%s). Output: %.300s", e.returncode, text)
            # Devolver stdout para diagnóstico aunque haya RC != 0
            return False, text
        except Exception as e:
            _LOGGER.error("CLI exception: %s", e)
            return False, ""

    # -------------------- Discovery & Status --------------------

    def list_devices(self) -> Dict[str, Dict[str, Any]]:
        self.ensure_client()
        cams = self._client.load_cameras()  # dict per serial
        result: Dict[str, Dict[str, Any]] = {}
        for serial, data in (cams or {}).items():
            name = (
                data.get("STATUS", {}).get("name")
                or data.get("deviceInfos", {}).get("deviceName")
                or "Device"
            )
            result[serial] = {"device_name": name}
        return result

    def get_status(self, serial: str) -> Dict[str, Any]:
        ok, out = self._run_cli(["camera", "--serial", serial, "status"])
        if not ok and not out:
            return {}
        try:
            # Endurecer parseo: quitar BOM/espacios; arrancar desde el primer "{"
            raw = (out or "").lstrip("\ufeff").strip()
            if not raw:
                raise ValueError("empty response")
            start = raw.find("{")
            if start > 0:
                raw = raw[start:]
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            preview = (out or "").replace("\n", " ")[:300]
            _LOGGER.error("Parse JSON status fallito: %s. Preview=%.300s", e, preview)
            return {}

    # -------------------- Sblocco --------------------

    def _try_unlock(self, serial: str, lock_no: int) -> bool:
        self.ensure_client()
        try:
            self._client.remote_unlock(serial, lock_no)
            _LOGGER.info("remote_unlock OK (serial=%s, lock_no=%s)", serial, lock_no)
            return True
        except Exception as e:
            _LOGGER.warning("remote_unlock KO (serial=%s, lock_no=%s): %s", serial, lock_no, e)
            return False

    def unlock_door_cli(self, serial: str) -> bool:
        ok, out = self._run_cli(["camera", "--serial", serial, "unlock-door"])
        if ok:
            _LOGGER.info("CLI unlock-door OK (serial=%s)", serial)
        return ok

    def unlock_gate_cli(self, serial: str) -> bool:
        ok, out = self._run_cli(["camera", "--serial", serial, "unlock-gate"])
        if ok:
            _LOGGER.info("CLI unlock-gate OK (serial=%s)", serial)
        return ok



    
