"""Sealed evidence and fixed kernel aggregate views."""

import hashlib
import json
import os
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from time import time_ns
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    from factorylab.kernel.termination import Termination


class LedgerIntegrityError(RuntimeError):
    """Evidence is unavailable when its authenticated chain is invalid."""


def _plain(value):
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (frozenset, set)):
        return sorted(_plain(item) for item in value)
    return value


def _canonical(value) -> bytes:
    return json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


class KeyStore:
    """Public key access remains sealed until the bound Termination is final."""

    def __init__(self, key: bytes | None = None) -> None:
        self.__key = Fernet.generate_key() if key is None else key
        self.__cipher = Fernet(self.__key)
        self.__owner = None
        self.__released = False

    @property
    def key(self) -> bytes:
        """Return the seal key only after authorized termination."""
        if not self.__released:
            raise PermissionError("ledger key is sealed")
        return self.__key

    @property
    def released(self) -> bool:
        """Report whether termination has released the seal."""
        return self.__released

    def _bind(self, owner: "Termination") -> None:
        from factorylab.kernel.termination import Termination

        if not isinstance(owner, Termination):
            raise PermissionError("only Termination may own the seal")
        if self.__owner is not None and self.__owner is not owner:
            raise PermissionError("seal already has a termination authority")
        self.__owner = owner

    def _release(self, owner: "Termination") -> bytes:
        if owner is None or owner is not self.__owner or not owner.final:
            raise PermissionError("only final Termination may release the seal")
        self.__released = True
        return self.__key

    def _encrypt(self, item: bytes) -> bytes:
        return self.__cipher.encrypt(item)

    def _decrypt(self, token: bytes) -> bytes:
        return self.__cipher.decrypt(token)


class Ledger:
    """Items are encrypted, ordered, and authenticated; live readers get only fixed views."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        manifest: dict | None = None,
        clock_ns: Callable[[], int] = time_ns,
        full_verify_every: int = 256,
        key_path: str | Path | None = None,
    ) -> None:
        if type(full_verify_every) is not int or full_verify_every < 1:
            raise ValueError("full_verify_every must be a positive integer")
        self.__full_every = full_verify_every
        self.__size = 0
        self.__last_line = b""
        self.__keys = KeyStore(self._persist_key(key_path))
        self.__clock = clock_ns
        self.__tokens: list[bytes] = []
        self.__genesis = hashlib.sha256(_canonical({"manifest": manifest or {}})).hexdigest()
        self.__header = {"format": 1, "genesis_hash": self.__genesis}
        self.__head = self.__genesis
        self.__path = Path(path) if path is not None else None
        self.__final = False
        self.__authority = None
        self.__wallet = None
        if self.__path is not None:
            fd = os.open(self.__path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                line = _canonical(self.__header) + b"\n"
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            self.__size = len(line)
            self.__last_line = line

    @staticmethod
    def _persist_key(key_path: str | Path | None) -> bytes | None:
        """Create a 0600 key file (or return None for a memory-only key).

        The file must not already exist: a world never reuses another world's
        key. The file's only purpose is post-mortem decryption after a crash;
        it does not resume a world. Reading it before termination is a breach
        of the non-intervention covenant, not something the kernel can prevent.
        """
        if key_path is None:
            return None
        key = Fernet.generate_key()
        fd = os.open(Path(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
        return key

    @property
    def key_store(self) -> KeyStore:
        """Expose a store whose public key accessor stays sealed until termination."""
        return self.__keys

    @property
    def final(self) -> bool:
        """A terminated ledger permanently rejects ordinary appends."""
        return self.__final

    def seal_key_released(self) -> bool:
        """Report seal state without exposing any item or key material."""
        return self.__keys.released

    def healthy(self) -> bool:
        """Cheap integrity check: persisted size and tail match what this ledger wrote.

        Every ``full_verify_every`` items it also walks the whole chain. Same-size
        in-place edits to an earlier line are caught by that periodic walk, by
        ``verify()``, and by ``aggregate()``; size changes, truncation, reordering
        of the tail and a forged header are caught immediately.
        """
        if len(self.__tokens) % self.__full_every == 0:
            return self.verify()
        if self.__path is None:
            return True
        try:
            if os.stat(self.__path).st_size != self.__size:
                return False
            with self.__path.open("rb") as stream:
                stream.seek(max(0, self.__size - len(self.__last_line)))
                return stream.read() == self.__last_line
        except OSError:
            return False

    def append(self, entry: dict) -> int:
        """Durably append one encrypted item; reject final or corrupted ledgers.

        Corruption detection here is ``healthy()``; call ``verify()`` for a full walk.
        """
        if self.__final:
            raise RuntimeError("world is final")
        if not self.healthy():
            raise LedgerIntegrityError("ledger verification failed")
        return self._append(entry)

    def _append(self, entry: dict) -> int:
        if not isinstance(entry, dict):
            raise TypeError("entry must be a dict")
        if {"seq", "prev_hash", "hash"} & entry.keys():
            raise ValueError("chain metadata belongs to the ledger")
        item = json.loads(_canonical(entry))
        item.setdefault("ts", self.__clock())
        if type(item["ts"]) is not int or item["ts"] < 0:
            raise ValueError("ts must be nonnegative integer nanoseconds")
        item.update(seq=len(self.__tokens), prev_hash=self.__head)
        item["hash"] = hashlib.sha256(_canonical(item)).hexdigest()
        token = self.__keys._encrypt(_canonical(item))
        if self.__path is not None:
            line = _canonical({"item": token.decode("ascii")}) + b"\n"
            with self.__path.open("ab") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            self.__size += len(line)
            self.__last_line = line
        self.__tokens.append(token)
        self.__head = item["hash"]
        return item["seq"]

    def _tokens(self) -> list[bytes]:
        if self.__path is None:
            return list(self.__tokens)
        with self.__path.open("rb") as stream:
            lines = stream.readlines()
        if not lines or any(not line.endswith(b"\n") for line in lines):
            raise LedgerIntegrityError("incomplete ledger")
        if json.loads(lines[0]) != self.__header:
            raise LedgerIntegrityError("genesis header changed")
        tokens = []
        for line in lines[1:]:
            record = json.loads(line)
            if set(record) != {"item"}:
                raise LedgerIntegrityError("invalid encrypted record")
            tokens.append(record["item"].encode("ascii"))
        return tokens

    def verify(self) -> bool:
        """Detect changed, reordered, truncated or unauthenticated items and headers."""
        try:
            tokens = self._tokens()
            if len(tokens) != len(self.__tokens):
                return False
            previous = self.__genesis
            for seq, token in enumerate(tokens):
                item = json.loads(self.__keys._decrypt(token))
                digest = item.pop("hash")
                if item["seq"] != seq or item["prev_hash"] != previous:
                    return False
                if hashlib.sha256(_canonical(item)).hexdigest() != digest:
                    return False
                previous = digest
            return previous == self.__head
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            InvalidToken,
            LedgerIntegrityError,
        ):
            return False

    def decrypt_item(self, seq: int) -> dict:
        """Return one authenticated item only after termination releases its key."""
        _ = self.__keys.key
        if type(seq) is not int or seq < 0:
            raise ValueError("seq must be a nonnegative integer")
        return json.loads(self.__keys._decrypt(self._tokens()[seq]))

    def aggregate(self, view: str, **params) -> dict:
        """Return a fixed aggregate with optional half-open timestamp bounds, never items."""
        views = (
            "wallet_series",
            "spend_by_capability",
            "invocations_by_assembly",
            "action_frequencies",
            "settlement_latency",
        )
        if view not in views or params.keys() - {"since_ns", "until_ns"}:
            raise ValueError("unknown aggregate or parameter")
        for value in params.values():
            if type(value) is not int or value < 0:
                raise ValueError("aggregate bounds must be nonnegative integer nanoseconds")
        since, until = params.get("since_ns", 0), params.get("until_ns")
        if until is not None and until < since:
            raise ValueError("inverted aggregate interval")
        if not self.verify():
            raise LedgerIntegrityError("ledger verification failed")
        items = [json.loads(self.__keys._decrypt(token)) for token in self._tokens()]
        selected = [
            item for item in items if item["ts"] >= since and (until is None or item["ts"] < until)
        ]
        if view == "wallet_series":
            return {
                "series": [
                    {"ts": item["ts"], "balance": item["balance_after"]}
                    for item in selected
                    if item.get("kind")
                    in ("wallet.initial", "wallet.commit", "wallet.drip", "wallet.settle")
                ]
            }
        if view == "spend_by_capability":
            choices = {
                item["handle"]: item["propensity"]["chosen"]
                for item in items
                if item.get("kind") == "decision.open"
            }
            amounts: Counter = Counter()
            for item in selected:
                if item.get("kind") == "wallet.commit":
                    capability = choices.get(item["handle"], item["reason"])
                    amounts[capability] += item["amount"]
            return {"spend": dict(sorted(amounts.items()))}
        if view == "invocations_by_assembly":
            counts = Counter(
                item["assembly_id"] for item in selected if item.get("kind") == "invocation"
            )
            return {"counts": dict(sorted(counts.items()))}
        if view == "action_frequencies":
            counts = Counter(
                item["propensity"]["chosen"]
                for item in selected
                if item.get("kind") == "decision.open"
            )
            return {"counts": dict(sorted(counts.items()))}
        latencies = [
            item["latency_ns"] for item in selected if item.get("kind") == "decision.settle"
        ]
        return {
            "count": len(latencies),
            "total_ns": sum(latencies),
            "min_ns": min(latencies, default=None),
            "max_ns": max(latencies, default=None),
        }

    def _claim_wallet(self, wallet) -> None:
        if self.__wallet is not None:
            raise ValueError("one wallet per ledger")
        self.__wallet = wallet

    def _bind_termination(self, authority: "Termination") -> None:
        self.__keys._bind(authority)
        self.__authority = authority

    def _terminate(self, authority: "Termination", entry: dict) -> None:
        if authority is not self.__authority or not authority.final:
            raise PermissionError("termination authority required")
        # Preserve the final event even when prior evidence has failed verification.
        self.__final = True
        try:
            self._append(entry)
        finally:
            self.__keys._release(authority)
