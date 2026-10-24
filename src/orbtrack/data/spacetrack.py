"""Space-Track.org client for orbit history (``gp_history``) and re-entries (``decay``).

Space-Track asks API users to stay under 30 requests a minute and 300 an hour, to
download history once and keep it, and then to fetch only what is new. This client
spaces requests out, batches satellites into one query, and leaves the caching to
the history store.
"""

import time
from collections import deque
from datetime import datetime
from urllib.parse import quote

import httpx

BASE_URL = "https://www.space-track.org"
PER_MINUTE = 25  # a little under the published 30
PER_HOUR = 250  # a little under the published 300
BATCH = 100  # NORAD ids per query


class SpaceTrackError(RuntimeError):
    pass


class SpaceTrack:
    """Use as a context manager: logs in on enter and out on exit."""

    def __init__(
        self,
        identity: str,
        password: str,
        *,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self._identity, self._password = identity, password
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=120, follow_redirects=True)
        self._sleep, self._clock = sleep, clock
        self._sent: deque[float] = deque()
        self.requests = 0

    def __enter__(self) -> "SpaceTrack":
        self._throttle()
        response = self._client.post(
            "/ajaxauth/login", data={"identity": self._identity, "password": self._password}
        )
        # A failed login still answers 200, with a JSON body naming the problem.
        if response.status_code != 200 or "Failed" in response.text:
            raise SpaceTrackError("Space-Track login failed: check SPACETRACK_IDENTITY / SPACETRACK_PASSWORD")
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._client.get("/ajaxauth/logout")
        finally:
            self._client.close()

    def _wait(self, now: float) -> float:
        """Seconds until one more request fits under both limits (0 if it fits now)."""
        last_minute = [t for t in self._sent if now - t < 60]
        if len(last_minute) >= PER_MINUTE:
            return 60 - (now - last_minute[0])
        last_hour = [t for t in self._sent if now - t < 3600]
        if len(last_hour) >= PER_HOUR:
            return 3600 - (now - last_hour[0])
        return 0.0

    def _throttle(self) -> None:
        while (wait := self._wait(self._clock())) > 0:
            self._sleep(wait)
        now = self._clock()
        while self._sent and now - self._sent[0] >= 3600:
            self._sent.popleft()
        self._sent.append(now)
        self.requests += 1

    def query(self, path: str) -> list[dict]:
        self._throttle()
        response = self._client.get(f"/basicspacedata/query/{path}/format/json")
        if response.status_code != 200:
            raise SpaceTrackError(f"Space-Track answered {response.status_code} for {path}")
        try:
            return response.json()
        except ValueError as e:
            raise SpaceTrackError(f"Space-Track sent something that is not JSON for {path}") from e

    def gp_history(self, norad_ids: list[int], since: datetime, until: datetime | None = None) -> list[dict]:
        """Every element set published for these satellites with an epoch after ``since``."""
        out = []
        for i in range(0, len(norad_ids), BATCH):
            ids = ",".join(str(n) for n in norad_ids[i : i + BATCH])
            epoch = _range(since, until)
            out += self.query(f"class/gp_history/NORAD_CAT_ID/{ids}/EPOCH/{epoch}/orderby/EPOCH%20asc")
        return out

    def decays(self, norad_ids: list[int]) -> list[dict]:
        """Recorded and predicted re-entries for these satellites."""
        out = []
        for i in range(0, len(norad_ids), BATCH):
            ids = ",".join(str(n) for n in norad_ids[i : i + BATCH])
            out += self.query(f"class/decay/NORAD_CAT_ID/{ids}/orderby/DECAY_EPOCH%20desc")
        return out


def _range(since: datetime, until: datetime | None) -> str:
    """Whole days are enough: the history store ignores element sets it already has."""
    text = f"{since:%Y-%m-%d}--{until:%Y-%m-%d}" if until else f">{since:%Y-%m-%d}"
    return quote(text, safe="-")
