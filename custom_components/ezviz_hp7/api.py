from __future__ import annotations
import json
import logging
import shutil
import subprocess
from typing import Any, Dict, Optional

from .pylocalapi.client import EzvizClient

_LOGGER = logging.getLogger(__name__)

# Elegí UNO solo. Si no funciona apiisa, cambiá por litedev y probá de nuevo.
SA_BACKEND = "apiisa.ezvizlife.com"  # alternativas: "litedev.ezvizlife.com"

DEFAULT_DOOR_LOCK_NO = 2   # PORTA=2
DEFAULT_GATE_LOCK_NO = 1   # CANCELLO=1


class Hp7Api:
    def __init__(self, username: str, password: str, region: str):
        self._username = username
        self._password = password

        reg_in = (region or "").strip()
        reg = reg_in.lower()

        # Valor que pasaremos al SDK y a la CLI (-r)
        if reg in ("sa", "br"):
            # Fuerza SIEMPRE el backend elegido arriba.
            self._region_or_url = SA_BACKEND
        elif "." in reg_in:
            # Si el usuario ingresó un FQDN, respetarlo tal cual (sin esquema).
            self._region_or_url = reg_in
        else:
            # eu/us/cn/as (lo maneja internamente el SDK)
            self._region_or_url = reg_in

        self._client: Optional[EzvizClient] = None
        self._user_id: Optional[str] = None
        self._cli = shutil.which("pyezvizapi")

        self.supports_door = True
        self.supports_gate = True

    # -------------------- Sessione SDK (solo per unlock) --------------------

    def ensure_client(self) -> None:
        if self._client is not None:
            return
        _LOGGER.debug("EZVIZ HP7: intentando login SDK con '%s'", self._region_or_url)
        self._client = EzvizClient(
            account=self._username,
            password=self._password,
            url=self._region_or_url,
        )
        try:
            self._client.login()
            _LOGGER.info("EZVIZ HP7: login OK en '%s'", self._client._token.get("api_url", self._region_or_url))
        except Exception as e:
            _LOGGER.error("EZVIZ HP7: login FAILED en '%s' -> %s", self._region_or_url, e)
            raise

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
        cmd = [self._cli, "-u", self._username, "-p", self._password, "-r", self._region_or_url] + args
        _LOGGER.debug("EZVIZ HP7: ejecutando CLI con -r '%s' args=%s", self._region_or_url, args)
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=30)
            text = out.decode("utf-8", "ignore")
            return True, text
        except subprocess.CalledProcessError as e:
            text = (e.output or b"").decode("utf-8", "ignore")
            _LOGGER.error("CLI error (%s). Output: %.300s", e.returncode, text)
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

    # ---- Desbloqueo inteligente: primero SDK, luego CLI como fallback ----
    def unlock_door(self, serial: str) -> bool:
        """Intenta abrir la PUERTA: primero SDK (lock 2, luego 1), después CLI."""
        self.ensure_client()
        # SDK primero
        if self._try_unlock(serial, DEFAULT_DOOR_LOCK_NO):  # 2
            _LOGGER.info("unlock_door SDK OK (serial=%s, lock_no=%s)", serial, DEFAULT_DOOR_LOCK_NO)
            return True
        if self._try_unlock(serial, DEFAULT_GATE_LOCK_NO):  # 1 (fallback)
            _LOGGER.info("unlock_door SDK OK (serial=%s, lock_no=%s)", serial, DEFAULT_GATE_LOCK_NO)
            return True
        # Fallback CLI
        ok, _ = self._run_cli(["camera", "--serial", serial, "unlock-door"])
        _LOGGER.log(logging.INFO if ok else logging.ERROR, "unlock_door CLI %s (serial=%s)", "OK" if ok else "FALLITO", serial)
        return ok

    def unlock_gate(self, serial: str) -> bool:
        """Intenta abrir el PORTÓN: primero SDK (lock 1, luego 2), después CLI."""
        self.ensure_client()
        # SDK primero
        if self._try_unlock(serial, DEFAULT_GATE_LOCK_NO):  # 1
            _LOGGER.info("unlock_gate SDK OK (serial=%s, lock_no=%s)", serial, DEFAULT_GATE_LOCK_NO)
            return True
        if self._try_unlock(serial, DEFAULT_DOOR_LOCK_NO):  # 2 (fallback)
            _LOGGER.info("unlock_gate SDK OK (serial=%s, lock_no=%s)", serial, DEFAULT_DOOR_LOCK_NO)
            return True
        # Fallback CLI
        ok, _ = self._run_cli(["camera", "--serial", serial, "unlock-gate"])
        _LOGGER.log(logging.INFO if ok else logging.ERROR, "unlock_gate CLI %s (serial=%s)", "OK" if ok else "FALLITO", serial)
        return ok

