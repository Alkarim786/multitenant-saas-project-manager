"""Fractional indexing: a string key strictly between any two neighbours.

Moving a card updates only that card's rank, so a drag touches one row and a
concurrent move elsewhere in the column can't conflict with it. Keys are compared
bytewise (the column uses the "C" collation), and the alphabet is in ASCII order.
"""

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(ALPHABET)
_VALUE = {c: i for i, c in enumerate(ALPHABET)}
MAX_LENGTH = 64


class RankExhausted(ValueError):
    """The gap has become too deep; the column needs rebalancing."""


def between(low: str | None, high: str | None) -> str:
    """A key with low < key < high. ``None`` means the open start or end of the list."""
    low = low or ""
    if high is not None and low >= high:
        raise ValueError(f"low {low!r} must sort before high {high!r}")
    out = []
    i = 0
    while True:
        a = _VALUE[low[i]] if i < len(low) else 0
        b = _VALUE[high[i]] if high is not None and i < len(high) else BASE
        if b - a > 1:
            out.append(ALPHABET[(a + b) // 2])
            key = "".join(out)
            break
        # No room at this digit: keep low's digit and look deeper. Past this point the
        # key already sorts below high, so high no longer constrains the next digits.
        out.append(ALPHABET[a])
        if b - a == 1:
            high = None
        i += 1
    if len(key) > MAX_LENGTH:
        raise RankExhausted("rank key too long")
    return key


def evenly_spaced(count: int) -> list[str]:
    """Fresh, well-separated keys for rebalancing a column."""
    keys: list[str] = []
    width = 1
    while BASE**width <= count:
        width += 1
    step = BASE**width // (count + 1)
    for n in range(1, count + 1):
        v = n * step
        digits = []
        for _ in range(width):
            v, d = divmod(v, BASE)
            digits.append(ALPHABET[d])
        # The suffix keeps the invariant that no key ends in the lowest digit, which is
        # what guarantees there is always room to insert before any key.
        keys.append("".join(reversed(digits)) + "U")
    return keys
