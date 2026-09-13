"""Exponential backoff with jitter and a hard ceiling.

When Kismet is down, PiSight must not hammer it: the Pi shares one CPU with the capture
engine, and a tight retry loop competes with packet processing. Backoff grows
exponentially to a configured maximum, and jitter spreads retries so a Kismet restart is
not met by a thundering herd of reconnects from several PiSight instances at once.

The random source is injectable so tests can assert exact delay sequences.
"""

from __future__ import annotations

import random

__all__ = ["ExponentialBackoff"]


class ExponentialBackoff:
    """Computes successive retry delays.

    Args:
        initial: delay after the first failure, in seconds.
        factor: multiplier applied per consecutive failure (>= 1.0).
        maximum: ceiling the delay never exceeds, before jitter.
        jitter: fraction of the delay randomised, in ``[0.0, 1.0]``. ``0.25`` means the
            returned delay lies within +/-25% of the nominal value.
        rng: random source; inject a seeded :class:`random.Random` in tests.
    """

    def __init__(
        self,
        *,
        initial: float = 1.0,
        factor: float = 2.0,
        maximum: float = 30.0,
        jitter: float = 0.25,
        rng: random.Random | None = None,
    ) -> None:
        if initial <= 0:
            raise ValueError("initial must be positive")
        if factor < 1.0:
            raise ValueError("factor must be >= 1.0")
        if maximum < initial:
            raise ValueError("maximum must be >= initial")
        if not 0.0 <= jitter <= 1.0:
            raise ValueError("jitter must be between 0.0 and 1.0")

        self._initial = initial
        self._factor = factor
        self._maximum = maximum
        self._jitter = jitter
        self._rng = rng if rng is not None else random.Random()  # noqa: S311 - retry timing
        self._failures = 0

    @property
    def failures(self) -> int:
        """Number of consecutive failures recorded since the last :meth:`reset`."""
        return self._failures

    def nominal_delay(self) -> float:
        """The un-jittered delay for the current failure count."""
        if self._failures <= 0:
            return 0.0
        delay = self._initial * (self._factor ** (self._failures - 1))
        return min(delay, self._maximum)

    def next_delay(self) -> float:
        """Record a failure and return the delay to wait before the next attempt.

        The result is always positive and never exceeds ``maximum * (1 + jitter)``.
        """
        self._failures += 1
        delay = self.nominal_delay()
        if self._jitter > 0.0:
            spread = delay * self._jitter
            delay += self._rng.uniform(-spread, spread)
        return max(0.05, delay)

    def reset(self) -> None:
        """Clear the failure count after a successful attempt."""
        self._failures = 0
