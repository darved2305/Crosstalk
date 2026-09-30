import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_VALUES = {char: index for index, char in enumerate(_ALPHABET)}


def new_ulid() -> str:
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_ALPHABET[(value >> shift) & 31] for shift in range(125, -1, -5))


def is_ulid(value: str) -> bool:
    return len(value) == 26 and value[0] in "01234567" and all(c in _VALUES for c in value)
