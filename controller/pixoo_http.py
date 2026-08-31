"""Robust HTTP client for the Pixoo 64 local API (http://<ip>/post).

Distinguishes host-unreachable / connection-refused / timeout / invalid-JSON /
error_code!=0, uses a pooled session with bounded retries + backoff, and never
retries forever. All commands are synchronous.
"""
from __future__ import annotations

import base64
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from PIL import Image

from common.logutil import get_logger, shorten
from .image import encode_frame

log = get_logger("controller.http")


class PixooError(RuntimeError):
    """error_code != 0 in a Pixoo response."""

    def __init__(self, code: int, command: str, raw: Any):
        super().__init__(f"Pixoo command {command!r} returned error_code={code}")
        self.code = code
        self.command = command
        self.raw = raw


class PixooUnreachable(RuntimeError):
    """Host down, port closed, or timed out."""


class PixooBadResponse(RuntimeError):
    """Response body was not valid JSON."""


class PixooHttp:
    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout: float = 2.0,
        read_timeout: float = 5.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ):
        self.base_url = base_url
        self.timeout = (connect_timeout, read_timeout)
        self._session = requests.Session()
        retry = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset({"POST"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    # -- low level -------------------------------------------------------
    def post(self, payload: dict) -> dict:
        command = payload.get("Command", "?")
        try:
            resp = self._session.post(
                self.base_url,
                json=payload,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            raise PixooUnreachable(f"{command}: {type(e).__name__}: {e}") from e
        try:
            data = resp.json()
        except ValueError as e:
            raise PixooBadResponse(
                f"{command}: non-JSON response: {shorten(resp.text)}"
            ) from e
        code = data.get("error_code", 0)
        if code != 0:
            raise PixooError(int(code), command, data)
        return data

    # -- health ----------------------------------------------------------
    def is_api_ready(self) -> bool:
        """True if the local API answers GetAllConf with error_code 0."""
        try:
            self.get_config()
            return True
        except (PixooUnreachable, PixooBadResponse, PixooError):
            return False

    # -- commands (task section 12) -------------------------------------
    def get_config(self) -> dict:
        return self.post({"Command": "Channel/GetAllConf"})

    def set_brightness(self, value: int) -> dict:
        value = int(value)
        if not (0 <= value <= 100):
            raise ValueError(f"brightness must be 0..100, got {value}")
        return self.post({"Command": "Channel/SetBrightness", "Brightness": value})

    def screen_on(self) -> dict:
        return self.post({"Command": "Channel/OnOffScreen", "OnOff": 1})

    def screen_off(self) -> dict:
        return self.post({"Command": "Channel/OnOffScreen", "OnOff": 0})

    def reset_gif_id(self) -> dict:
        return self.post({"Command": "Draw/ResetHttpGifId"})

    def set_clock(self, clock_id: int) -> dict:
        """Select a clock face. Used to force a LOCAL face (e.g. 26) so the device
        never boots into a cloud face that hangs the firmware with no internet."""
        return self.post({"Command": "Channel/SetClockSelectId", "ClockId": int(clock_id)})

    def get_current_clock_id(self) -> int | None:
        try:
            return int(self.get_config().get("CurClockId"))
        except (ValueError, TypeError, PixooError, PixooUnreachable, PixooBadResponse):
            return None

    def send_frame(self, image: Image.Image, *, pic_id: int = 1, pic_speed: int = 1000) -> dict:
        """Send a single static 64x64 frame."""
        pic_data = encode_frame(image)
        # sanity: base64 must decode to exactly 12288 bytes
        assert len(base64.b64decode(pic_data)) == 64 * 64 * 3
        return self.post(
            {
                "Command": "Draw/SendHttpGif",
                "PicNum": 1,
                "PicWidth": 64,
                "PicOffset": 0,
                "PicID": pic_id,
                "PicSpeed": pic_speed,
                "PicData": pic_data,
            }
        )

    def close(self) -> None:
        self._session.close()
