"""The time machine: a simulated clock that can run fast, run backwards or stand still.

Simulated time is a straight line through an anchor: ``sim = sim0 + (real - real0) * rate``.
Every change re-anchors at the current moment, so the browser can follow the same
line from the three numbers in ``state()`` without asking the server every frame.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

RATES = [-1000, -300, -60, -10, -1, 1, 10, 60, 300, 1000]
WINDOW_S = 48 * 3600  # how far from now the clock may travel; TLEs fade beyond a couple of days


@dataclass
class SimClock:
    now_fn: Callable[[], float] = time.time
    rate: float = 1.0
    paused: bool = False
    sim0: float = field(default=None)  # type: ignore[assignment]
    real0: float = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        real = self.now_fn()
        self.sim0 = real if self.sim0 is None else self.sim0
        self.real0 = real if self.real0 is None else self.real0

    @property
    def effective_rate(self) -> float:
        return 0.0 if self.paused else self.rate

    def now(self) -> float:
        """Simulated Unix time in seconds."""
        return self.sim0 + (self.now_fn() - self.real0) * self.effective_rate

    def offset(self) -> float:
        """Seconds the simulated clock is ahead of (or behind, if negative) real time."""
        return self.now() - self.now_fn()

    @property
    def is_live(self) -> bool:
        return not self.paused and self.rate == 1 and abs(self.offset()) < 2

    def _anchor(self, sim: float | None = None) -> None:
        real = self.now_fn()
        sim = self.now() if sim is None else sim
        self.sim0 = min(max(sim, real - WINDOW_S), real + WINDOW_S)
        self.real0 = real

    def set_rate(self, rate: float) -> None:
        self._anchor()
        self.rate = rate
        self.paused = False

    def step(self, direction: int) -> None:
        """Move one notch along RATES: faster forwards for +1, towards rewinding for -1."""
        nearest = min(range(len(RATES)), key=lambda i: abs(RATES[i] - self.rate))
        index = min(max(nearest + (1 if direction > 0 else -1), 0), len(RATES) - 1)
        self.set_rate(RATES[index])

    def toggle(self) -> None:
        self._anchor()
        self.paused = not self.paused

    def jump(self, sim: float) -> None:
        """Travel to a simulated Unix time (kept inside the window), keeping rate and pause."""
        self._anchor(sim)

    def shift(self, seconds: float) -> None:
        self.jump(self.now() + seconds)

    def live(self) -> None:
        self.rate, self.paused = 1.0, False
        self._anchor(self.now_fn())

    def clamp(self) -> bool:
        """Stop at the edge of the window. Returns True if it had to."""
        if abs(self.offset()) <= WINDOW_S:
            return False
        self._anchor()
        self.paused = True
        return True

    def frame_period(self) -> float:
        """Real seconds between keyframe pushes: often at high rates, so a keyframe span stays short."""
        rate = abs(self.effective_rate)
        return 2.0 if rate == 0 else min(max(300 / rate, 0.4), 2.0)

    def frame_span(self) -> float:
        """Simulated seconds between the two keyframes of a frame, enough to outlast the next push."""
        rate = self.effective_rate
        return 4.0 if rate == 0 else 2.2 * self.frame_period() * rate

    def state(self) -> dict:
        return {
            "sim0_ms": self.sim0 * 1000,
            "real0_ms": self.real0 * 1000,
            "rate": self.effective_rate,
            "live": self.is_live,
        }
