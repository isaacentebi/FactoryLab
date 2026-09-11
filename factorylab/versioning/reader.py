"""Authenticated diary prefixes are available without importing the runtime."""

import hashlib
import json
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class DiaryError(ValueError):
    """An unreadable or unauthenticated diary never yields partial results."""


def _object(pairs: list[tuple]) -> dict:
    """Ambiguous duplicate JSON keys are rejected."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise DiaryError("duplicate JSON key")
        result[key] = value
    return result


def _json(raw: bytes) -> dict:
    """Only JSON objects with unambiguous keys are accepted."""
    value = json.loads(raw, object_pairs_hook=_object)
    if not isinstance(value, dict):
        raise DiaryError("expected a JSON object")
    return value


def read_diary(ledger_path: str | Path, key_path: str | Path) -> list[dict]:
    """Return plain items only if every token and canonical hash link verifies.

    File order must already be sequence order. Incomplete lines are rejected.
    Without an independently retained head/count, removal of complete tail items
    cannot be distinguished from a valid shorter diary. File reads are the sole
    I/O boundary; analysis functions never read files or modify their inputs.
    """
    try:
        cipher = Fernet(Path(key_path).read_bytes().strip())
        with Path(ledger_path).open("rb") as stream:
            header_line = stream.readline()
            if not header_line.endswith(b"\n"):
                raise DiaryError("missing or incomplete header")
            header = _json(header_line)
            if set(header) != {"format", "genesis_hash"} or type(header["format"]) is not int:
                raise DiaryError("invalid diary header")
            previous = header["genesis_hash"]
            if (
                header["format"] != 1
                or not isinstance(previous, str)
                or len(previous) != 64
                or any(char not in "0123456789abcdef" for char in previous)
            ):
                raise DiaryError("invalid diary format or genesis hash")
            items = []
            for seq, line in enumerate(stream):
                if not line.endswith(b"\n"):
                    raise DiaryError(f"incomplete record at seq {seq}")
                record = _json(line)
                if set(record) != {"item"}:
                    raise DiaryError(f"invalid record at seq {seq}")
                item = _json(cipher.decrypt(record["item"].encode("ascii")))
                digest = item.pop("hash")
                if type(item["seq"]) is not int or item["seq"] != seq:
                    raise DiaryError(f"invalid sequence at seq {seq}")
                if item["prev_hash"] != previous:
                    raise DiaryError(f"broken previous hash at seq {seq}")
                canonical = json.dumps(
                    item, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
                if hashlib.sha256(canonical).hexdigest() != digest:
                    raise DiaryError(f"invalid hash at seq {seq}")
                item["hash"] = digest
                items.append(item)
                previous = digest
            return items
    except DiaryError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, InvalidToken) as exc:
        raise DiaryError("diary could not be read and authenticated") from exc
