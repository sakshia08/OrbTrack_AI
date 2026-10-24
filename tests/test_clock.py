import pytest

from orbtrack.app.clock import RATES, WINDOW_S, SimClock


class FakeTime:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def real():
    return FakeTime()


@pytest.fixture
def clock(real):
    return SimClock(now_fn=real)


def test_starts_live(clock, real):
    assert clock.is_live
    real.t += 10
    assert clock.now() == pytest.approx(real.t)


def test_rate_and_pause_reanchor_without_jumping(clock, real):
    real.t += 10
    clock.set_rate(60)
    assert clock.now() == pytest.approx(real.t)  # changing speed never teleports
    real.t += 10
    assert clock.offset() == pytest.approx(590)
    clock.toggle()
    before = clock.now()
    real.t += 100
    assert clock.now() == pytest.approx(before)
    assert clock.state()["rate"] == 0 and not clock.is_live


def test_step_walks_the_rate_ladder(clock):
    clock.step(+1)
    assert clock.rate == 10
    for _ in range(20):
        clock.step(+1)
    assert clock.rate == RATES[-1]
    for _ in range(20):
        clock.step(-1)
    assert clock.rate == RATES[0]


def test_jump_and_live(clock, real):
    clock.jump(real.t + 3600)
    assert clock.offset() == pytest.approx(3600)
    clock.jump(real.t + 10 * WINDOW_S)
    assert clock.offset() == pytest.approx(WINDOW_S)  # kept inside the window
    clock.live()
    assert clock.is_live and clock.offset() == pytest.approx(0)


def test_clamp_pauses_at_the_edge(clock, real):
    clock.set_rate(1000)
    real.t += WINDOW_S / 1000 + 5
    assert clock.clamp()
    assert clock.paused and clock.offset() == pytest.approx(WINDOW_S)
    assert not clock.clamp()


def test_state_lets_the_browser_follow_the_same_line(clock, real):
    clock.set_rate(-300)
    s = clock.state()
    real.t += 7
    assert s["sim0_ms"] + (real.t * 1000 - s["real0_ms"]) * s["rate"] == pytest.approx(clock.now() * 1000)


@pytest.mark.parametrize("rate", [1, 10, 60, 300, 1000, -1000])
def test_keyframe_span_outlasts_the_next_push_and_stays_short(clock, rate):
    clock.set_rate(rate)
    assert abs(clock.frame_span()) > abs(rate) * clock.frame_period() * 2  # never runs out between pushes
    assert abs(clock.frame_span()) <= 900  # ~1/6 of a LEO orbit, so slerp stays accurate
