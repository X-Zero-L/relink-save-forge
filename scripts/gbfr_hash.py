"""GBFR custom XXHash32 variant for text IDs.

Ported from the public GBFRDataTools implementation. This is not the save-file
payload integrity hash; see docs/SAVE_SAFETY.md for that distinction.

The upstream implementation uses a ``do/while`` loop for its 16-byte path.
That detail matters: an input of exactly 16 bytes must process one long block.
"""

PRIME32_1 = 0x9E3779B1
PRIME32_2 = 0x85EBCA77
PRIME32_3 = 0xC2B2AE3D
PRIME32_4 = 0x27D4EB2F
PRIME32_5 = 0x165667B1


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


def _rotl32(value: int, bits: int) -> int:
    value &= 0xFFFFFFFF
    return ((value << bits) | (value >> (32 - bits))) & 0xFFFFFFFF


def _round(seed: int, input_value: int) -> int:
    return _u32(_rotl32(seed + _u32(input_value * PRIME32_2), 13) * PRIME32_1)


def gbfr_hash_bytes(data: bytes) -> int:
    view = memoryview(data)
    result = 0x178A54A4
    offset = 0
    length = len(data)
    if length >= 16:
        v1 = 0x2557311B
        v2 = 0x871FB76A
        v3 = 0x0133ECF3
        v4 = 0x62FC7342
        # GBFRDataTools uses do/while here. A normal pre-checked ``> 16`` loop
        # silently computes the wrong value for every exactly-16-byte GBID.
        while True:
            v1 = _round(v1, int.from_bytes(view[offset : offset + 4], "little"))
            offset += 4
            v2 = _round(v2, int.from_bytes(view[offset : offset + 4], "little"))
            offset += 4
            v3 = _round(v3, int.from_bytes(view[offset : offset + 4], "little"))
            offset += 4
            v4 = _round(v4, int.from_bytes(view[offset : offset + 4], "little"))
            offset += 4
            if length - offset <= 16:
                break
        result = _u32(
            _rotl32(v1, 1) + _rotl32(v2, 7) + _rotl32(v3, 12) + _rotl32(v4, 18)
        )
    result = _u32(result + length)
    while length - offset >= 4:
        value = int.from_bytes(view[offset : offset + 4], "little")
        result = _u32(_rotl32(result + _u32(value * PRIME32_3), 17) * PRIME32_4)
        offset += 4
    while length - offset > 0:
        result = _u32(_rotl32(result + view[offset] * PRIME32_5, 11) * PRIME32_1)
        offset += 1
    result ^= result >> 15
    result = _u32(result * PRIME32_2)
    result ^= result >> 13
    result = _u32(result * PRIME32_3)
    result ^= result >> 16
    return result & 0xFFFFFFFF


def gbfr_hash(text: str, encoding: str = "ascii") -> int:
    return gbfr_hash_bytes(str(text).encode(encoding, errors="ignore"))


def gbfr_hash_hex(text: str) -> str:
    return f"{gbfr_hash(text):08X}"
