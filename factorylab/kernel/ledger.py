"""Sealed evidence and fixed kernel aggregate views."""

import fcntl
import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import fields, is_dataclass
from pathlib import Path
from time import time_ns
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    from factorylab.kernel.termination import Termination


class LedgerIntegrityError(RuntimeError):
    """Evidence is unavailable when its authenticated chain is invalid."""


class GenesisMismatchError(LedgerIntegrityError):
    """This ledger was opened with a genesis header the caller's manifest does not produce.

    An integrity error like any other, and separable from one: it is the single
    failure an operator can fix, by resuming the world with the manifest it was
    created from.
    """


class LedgerBusyError(RuntimeError):
    """Another runtime already holds the exclusive writer lock."""


class LedgerLock:
    """Hold one OS writer lock until close or process exit; never unlink its inode.

    Ownership never leaves this process. The descriptor is close-on-exec from
    the syscall that creates it, so a child — a jailed population tool above
    all — can neither inherit the lock nor keep a dead world locked after its
    runtime is gone: an ``flock`` lives on the open file description, and a
    description no survivor holds dies with the process that opened it.
    """

    def __init__(self, path: str | Path | None) -> None:
        self.fd = None
        if path is None:
            return
        lock_path = str(Path(path).resolve()) + ".lock"
        fd = os.open(lock_path,
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.set_inheritable(fd, False)  # explicit, and a no-op where O_CLOEXEC held
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise LedgerBusyError("ledger already in use") from None
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd

    def close(self) -> None:
        """Release ownership exactly once; process death also releases the OS lock."""
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()


def utf8_text(text: str) -> str:
    """Return ``text`` unchanged when it is UTF-8 encodable; otherwise the same text with
    each lone UTF-16 surrogate replaced by U+FFFD, so the result always encodes.

    Idempotent, so an item written through it compares equal to itself on replay.
    """
    if text.isascii():  # No ASCII string carries a surrogate, so none can fail to encode.
        return text
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return text


def _plain(value):
    # The exact builtin types below take the same branch as the isinstance chain
    # that follows, and no other: dispatching on them first only spares the walk
    # the abstract checks, never a different answer.
    kind = type(value)
    if kind is str:
        return utf8_text(value)
    if kind is int or kind is float or kind is bool or value is None:
        return value
    if kind is dict:
        return _plain_mapping(value)
    if kind is list or kind is tuple:
        return [_plain(item) for item in value]
    if isinstance(value, str):
        return utf8_text(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return _plain_mapping(value)
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (frozenset, set)):
        return sorted(_plain(item) for item in value)
    return value


def _plain_mapping(value):
    for key in value:
        if type(key) is not str and not isinstance(key, str):
            raise TypeError("JSON object keys must be strings")
    return {utf8_text(key): _plain(item) for key, item in value.items()}


def canonical(value) -> bytes:
    """Return the one byte encoding every hash in the factory is taken over.

    Sorted keys, no insignificant whitespace, no NaN and no lone surrogate.
    Three packages hash with it, so it is the kernel's public canonicalisation
    and not an implementation detail of the ledger's own chain.
    """
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
        self.__tokens: list[bytes] = []  # memory-only ledgers; disk diaries stream
        self.__count = 0
        self.__decision_count = 0
        self.__read_only = False
        self.__read_cursor = (0, None)
        self.__checkpoint: dict | None = None
        self.__raw_hash = hashlib.sha256()
        self.__index = self._empty_index()
        self.__persist_head = key_path is not None
        self.__decision_ids: dict[int, str] = {}
        self.__genesis = hashlib.sha256(canonical({"manifest": manifest or {}})).hexdigest()
        self.__header = {"format": 1, "genesis_hash": self.__genesis}
        self.__head = self.__genesis
        self.__verified_tokens: tuple[bytes, ...] = ()
        self.__verified_head = self.__genesis
        self.__verified_count = -1  # No item count has had its full walk yet.
        self.__path = Path(path) if path is not None else None
        self.__world = manifest.get("name") if isinstance(manifest, dict) else None
        self.__final = False
        self.__authority = None
        self.__wallet = None
        if self.__path is not None:
            fd = os.open(self.__path,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, "wb") as stream:
                line = canonical(self.__header) + b"\n"
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            self.__size = len(line)
            self.__last_line = line
            self.__raw_hash.update(line)

    @staticmethod
    def _persist_key(key_path: str | Path | None) -> bytes | None:
        """Create a 0600 key file (or return None for a memory-only key).

        The file must not already exist: a world never reuses another world's
        key. The file permits process recovery and post-mortem decryption.
        Automatic recovery may read it without releasing the public seal.
        """
        if key_path is None:
            return None
        key = Fernet.generate_key()
        fd = os.open(Path(key_path),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())
        return key

    @classmethod
    def reopen(
        cls, path: str | Path, *, manifest: dict, clock_ns: Callable[[], int] = time_ns,
        full_verify_every: int = 1024, read_only: bool = False,
    ) -> "Ledger":
        """Verify a bounded stream before recovery, retaining only indexes and the current head.

        An encrypted head caches authentication of an exact byte prefix. Its digest
        is checked against the whole persisted prefix; only the tail is decrypted.
        A missing/stale head falls back to a streaming chain walk. Only an
        unterminated last line may be repaired, after prefix authentication.
        Read-only consumers freeze one byte boundary, allow final worlds and never repair.
        """
        ledger = cls(manifest=manifest, clock_ns=clock_ns, full_verify_every=full_verify_every)
        ledger_path = Path(path)
        try:
            ledger.__keys = KeyStore(Path(str(path) + ".key").read_bytes().strip())
            size = ledger_path.stat().st_size
            with ledger_path.open("rb") as stream:
                boundary = cls._acknowledged_boundary(stream, size)
            if read_only and boundary != size:
                raise LedgerIntegrityError("incomplete ledger")
            ledger.__path = ledger_path
            ledger.__size = boundary
            ledger.__read_only = read_only
            ledger.__persist_head = not read_only
            ledger.__checkpoint = ledger._load_head(boundary)
            checked, hasher = ledger._scan_disk(boundary)
            ledger.__count, ledger.__head = checked["count"], checked["head"]
            ledger.__decision_count = checked["decisions"]
            ledger.__index = checked["index"]
            ledger.__checkpoint, ledger.__raw_hash = checked, hasher
            if ledger.__index["terminated"] and not read_only:
                raise LedgerIntegrityError("cannot resume a terminated world")
            ledger.__last_line = next(ledger._reverse_lines(), b"")
            discarded = size - boundary
            if discarded:
                with ledger_path.open("r+b") as stream:
                    stream.truncate(boundary)
                    stream.flush()
                    os.fsync(stream.fileno())
                ledger.append({"kind": "ledger.repaired", "discarded_bytes": discarded,
                               "acknowledged_bytes": boundary})
        except (OSError, ValueError, KeyError, TypeError, InvalidToken) as exc:
            raise LedgerIntegrityError("ledger/key unavailable or manifest hash differs") from exc
        return ledger

    @staticmethod
    def _acknowledged_boundary(stream, size: int) -> int:
        position = size
        while position:
            start = max(0, position - 65536)
            stream.seek(start)
            chunk = stream.read(position - start)
            last = chunk.rfind(b"\n")
            if last >= 0:
                return start + last + 1
            position = start
        return 0

    @staticmethod
    def _empty_index() -> dict:
        return {"choices": {}, "wallet_series": [], "spend": {}, "invocations": {},
                "actions": {}, "latency_count": 0, "latency_total": 0,
                "latency_min": None, "latency_max": None,
                "first_tick": None, "last_event": None, "launch": False, "terminated": False,
                "launch_nonce": None, "release_digest": None, "facilitator_url": None}

    @staticmethod
    def _copy_index(index: dict) -> dict:
        """Return an index detached from its original: indexing one never reaches the other.

        ``_index_item`` only ever appends to ``wallet_series`` and assigns into the
        four counter maps, so fresh containers for those are the whole of detachment.
        The observations inside them are written once and never edited, and the one
        public view of them, ``aggregate``, deep-copies what it hands out.
        """
        copied = dict(index)
        series = index.get("wallet_series")
        if type(series) is list:
            copied["wallet_series"] = list(series)
        for name in ("choices", "spend", "invocations", "actions"):
            counter = index.get(name)
            if type(counter) is dict:
                copied[name] = dict(counter)
        return copied

    @staticmethod
    def _index_item(index: dict, item: dict) -> None:
        kind = item.get("kind")
        if kind in ("wallet.initial", "wallet.commit", "wallet.drip", "wallet.settle",
                    "wallet.settle_uncertain"):
            index["wallet_series"].append({"ts": item["ts"], "balance": item["balance_after"]})
        if kind == "decision.open":
            choice = item["propensity"]["chosen"]
            index["choices"][item["handle"]] = choice
            index["actions"][choice] = index["actions"].get(choice, 0) + 1
        if kind == "wallet.commit":
            choice = index["choices"].get(item["handle"], item["reason"])
            index["spend"][choice] = index["spend"].get(choice, 0) + item["amount"]
        if kind == "invocation":
            name = item["assembly_id"]
            index["invocations"][name] = index["invocations"].get(name, 0) + 1
        if kind == "decision.settle":
            latency = item["latency_ns"]
            index["latency_count"] += 1
            index["latency_total"] += latency
            index["latency_min"] = min(latency, index["latency_min"] if
                                        index["latency_min"] is not None else latency)
            index["latency_max"] = max(latency, index["latency_max"] if
                                        index["latency_max"] is not None else latency)
        if kind == "event":
            event = item["event"]
            index["last_event"] = event["ts_ns"]
            if event["kind"] == "Tick" and index["first_tick"] is None:
                index["first_tick"] = event["ts_ns"]
            index["launch"] |= event["kind"] == "Launch"
            index["terminated"] |= event["kind"] == "Terminated"
            if event["kind"] == "Launch":
                # The identity the world launched under, kept where a kill can read
                # it without walking the diary (runtime/witness.py).
                payload = event.get("payload") or {}
                index["launch_nonce"] = payload.get("launch_nonce")
                index["release_digest"] = payload.get("release_digest")
                index["facilitator_url"] = payload.get("facilitator_url")

    def _load_head(self, size: int) -> dict | None:
        try:
            data = json.loads(self.__keys._decrypt(
                Path(str(self.__path) + ".head").read_bytes()))
            if (data["genesis"] == self.__genesis and type(data["offset"]) is int
                    and data["offset"] > size):
                raise LedgerIntegrityError("ledger shorter than authenticated head")
            if (data["genesis"] == self.__genesis and data["format"] == 1
                    and type(data["offset"]) is int and 0 < data["offset"] <= size
                    and type(data["count"]) is int and data["count"] >= 0
                    and type(data["decisions"]) is int and data["decisions"] >= 0):
                return data
        except (OSError, InvalidToken, ValueError, KeyError, TypeError):
            pass  # Optional acceleration never supplies unauthenticated evidence.
        return None

    def _head_state(self) -> dict:
        return {"format": 1, "genesis": self.__genesis, "offset": self.__size,
                "digest": self.__raw_hash.hexdigest(), "head": self.__head,
                "count": self.__count, "decisions": self.__decision_count,
                "index": self._copy_index(self.__index)}

    def checkpoint(self) -> None:
        """Persist an authenticated prefix digest and kernel indexes without changing the diary."""
        if self.__path is None or not self.__persist_head or self.__read_only:
            return
        data = self._head_state()
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".ledger-head-", dir=self.__path.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(self.__keys._encrypt(canonical(data)))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, str(self.__path) + ".head")
            self.__checkpoint = data
        except OSError:
            pass  # Losing this cache only makes the next recovery verify the prefix again.
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def _token(line: bytes) -> bytes:
        if not line.endswith(b"\n"):
            raise LedgerIntegrityError("incomplete ledger")
        record = json.loads(line)
        if set(record) != {"item"}:
            raise LedgerIntegrityError("invalid encrypted record")
        return record["item"].encode("ascii")

    def _scan_disk(self, size: int) -> tuple[dict, object]:
        """Authenticate every byte while decrypting only records beyond the trusted head."""
        digest = hashlib.sha256()
        with self.__path.open("rb") as stream:
            header = stream.readline()
            if not header.endswith(b"\n") or json.loads(header) != self.__header:
                raise GenesisMismatchError("genesis header changed")
            digest.update(header)
            checkpoint = self.__checkpoint
            if checkpoint is not None and len(header) <= checkpoint["offset"] <= size:
                remaining = checkpoint["offset"] - len(header)
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        raise LedgerIntegrityError("incomplete ledger")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if digest.hexdigest() != checkpoint["digest"]:
                    raise LedgerIntegrityError("verified ledger prefix changed")
                previous, count = checkpoint["head"], checkpoint["count"]
                decisions, index = checkpoint["decisions"], self._copy_index(checkpoint["index"])
            else:
                previous, count, decisions, index = self.__genesis, 0, 0, self._empty_index()
            while stream.tell() < size:
                line = stream.readline()
                if not line or stream.tell() > size:
                    raise LedgerIntegrityError("incomplete ledger")
                token = self._token(line)
                item = json.loads(self.__keys._decrypt(token))
                claimed = item.pop("hash")
                if (item["seq"] != count or item["prev_hash"] != previous
                        or hashlib.sha256(canonical(item)).hexdigest() != claimed):
                    raise LedgerIntegrityError("ledger chain differs")
                self._index_item(index, item)
                decisions += item.get("kind") == "decision.handle"
                count += 1
                previous = claimed
                digest.update(line)
            if stream.tell() != size:
                raise LedgerIntegrityError("incomplete ledger")
        return {"format": 1, "genesis": self.__genesis, "offset": size,
                "digest": digest.hexdigest(), "head": previous, "count": count,
                "decisions": decisions, "index": index}, digest

    def _reverse_lines(self) -> Iterator[bytes]:
        """Yield complete lines newest first with memory bounded by the largest record."""
        with self.__path.open("rb") as stream:
            position, pending = max(0, self.__size - 1), b""
            while position:
                start = max(0, position - 65536)
                stream.seek(start)
                parts = (stream.read(position - start) + pending).split(b"\n")
                pending = parts[0]
                for line in reversed(parts[1:]):
                    yield line + b"\n"
                position = start
            if pending:
                yield pending + b"\n"

    def _iter_items(self, *, start_offset: int | None = None,
                    end_offset: int | None = None) -> Iterator[dict]:
        if self.__path is None:
            for token in self.__tokens:
                yield json.loads(self.__keys._decrypt(token))
            return
        with self.__path.open("rb") as stream:
            if start_offset is None:
                stream.readline()
            else:
                stream.seek(start_offset)
            end = self.__size if end_offset is None else end_offset
            while stream.tell() < end:
                yield json.loads(self.__keys._decrypt(self._token(stream.readline())))

    def _recovery_tail(self) -> tuple[dict | None, Iterator[dict]]:
        """Find the latest checkpoint backwards and stream its continuation one item at a time."""
        if not self.verify():
            raise LedgerIntegrityError("ledger verification failed")
        ordinal, offset = self.__decision_count, self.__size
        if self.__path is None:
            lines = ((None, token) for token in reversed(self.__tokens))
        else:
            lines = ((line, self._token(line)) for line in self._reverse_lines()
                     if json.loads(line).keys() == {"item"})
        for line, token in lines:
            if line is not None:
                offset -= len(line)
            item = json.loads(self.__keys._decrypt(token))
            if item.get("kind") == "snapshot":
                if line is None:
                    return item, (entry for entry in self._iter_items()
                                  if entry["seq"] > item["seq"])
                return item, self._iter_items(start_offset=offset + len(line),
                                              end_offset=self.__size)
            if item.get("kind") == "decision.handle":
                ordinal -= 1
                self.__decision_ids[item["seq"]] = f"decision-{ordinal}"
        return None, iter(())

    def _recovery_items(self) -> list[dict]:
        """Explicit internal exports authenticate items without releasing the public key."""
        if not self.verify():
            raise LedgerIntegrityError("ledger verification failed")
        return list(self._iter_items())

    @classmethod
    def open_read_only(cls, path: str | Path, *, manifest: dict) -> "Ledger":
        """Open a frozen ledger for reading: one authenticated byte boundary, no repair.

        The boundary is fixed at open time, so a writer appending underneath does
        not change what this reader sees, and a terminated world opens normally.
        Appends are refused. Nothing here releases the public seal.
        """
        return cls.reopen(path, manifest=manifest, read_only=True)

    def items(self) -> Iterator[dict]:
        """Yield every item up to this ledger's own boundary, decrypting one at a time.

        Streaming keeps a reader's memory bounded by the largest single item
        rather than by the diary. A writable disk ledger refuses, because its
        boundary moves under the reader; the chain itself is authenticated by
        ``verify()``, which ``aggregate()`` calls before every view.
        """
        if self.__path is not None and not self.__read_only:
            raise PermissionError("open the ledger read-only to iterate its items")
        return self._iter_items()

    def decision_id(self, seq: int) -> str:
        """Return the handle ordinal for a new append or an authenticated replay-tail item."""
        return self.__decision_ids[seq]

    def event_times(self) -> dict:
        """Only verified event boundaries and launch/finality flags leave the kernel index."""
        if not self.verify():
            raise LedgerIntegrityError("ledger verification failed")
        return {k: self.__index[k] for k in ("first_tick", "last_event", "launch", "terminated")}

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

    @property
    def path(self) -> Path | None:
        """Where the diary lives, or None for a memory-only ledger."""
        return self.__path

    def identity(self) -> dict:
        """What names this world outside its diary: the launch identity and whether it ended.

        The nonce, the release digest and the x402 facilitator come from the indexed
        ``Launch`` event, so a reopened ledger knows them without decrypting its
        history. ``terminated`` is true once a ``Terminated`` event is in the diary,
        whether appended by this process or found on disk.
        """
        return {"world": self.__world,
                "launch_nonce": self.__index.get("launch_nonce"),
                "release_digest": self.__index.get("release_digest"),
                "facilitator_url": self.__index.get("facilitator_url"),
                "terminated": bool(self.__final or self.__index.get("terminated"))}

    @property
    def diary_id(self) -> str | None:
        """The hash of the first sealed record: one value for every copy of this diary.

        Two deterministic worlds of one manifest and seed share a launch nonce by
        design; their diaries are sealed under different keys, so their first
        records differ. None before the first record exists.
        """
        if self.__path is None:
            token = self.__tokens[0] if self.__tokens else None
        else:
            try:
                with self.__path.open("rb") as stream:
                    stream.readline()  # the plaintext genesis header
                    first = stream.readline()
                token = self._token(first) if first.endswith(b"\n") else None
            except (OSError, LedgerIntegrityError, ValueError, AttributeError):
                return None
        return hashlib.sha256(token).hexdigest() if token else None

    def byte_hash(self) -> str | None:
        """SHA-256 of every diary byte written or verified so far; None for a memory-only ledger."""
        return self.__raw_hash.hexdigest() if self.__path is not None else None

    def healthy(self) -> bool:
        """Cheap integrity check: persisted size and tail match what this ledger wrote.

        Every ``full_verify_every`` items it also walks the whole chain, once. A
        second question about the same unappended prefix is answered by the walk
        that prefix already had, so the cadence is one walk per
        ``full_verify_every`` items rather than one per caller, and the cheap
        check still runs on every call. Same-size in-place edits to an earlier
        line are caught by that periodic walk, by ``verify()``, and by
        ``aggregate()``; size changes, truncation, reordering of the tail and a
        forged header are caught immediately.
        """
        if self.__count % self.__full_every == 0 and self.__verified_count != self.__count:
            if not self.verify():
                return False
            self.__verified_count = self.__count
        return self._tail_intact()

    def _tail_intact(self) -> bool:
        if self.__path is None:
            return True
        try:
            descriptor = os.open(self.__path, os.O_RDONLY | os.O_CLOEXEC)
        except OSError:
            return False
        try:
            if os.fstat(descriptor).st_size != self.__size:
                return False
            tail = len(self.__last_line)
            return os.pread(descriptor, tail, max(0, self.__size - tail)) == self.__last_line
        except OSError:
            return False
        finally:
            os.close(descriptor)

    def append(self, entry: dict) -> int:
        """Durably append one encrypted item; reject final or corrupted ledgers.

        Corruption detection here is ``healthy()``; call ``verify()`` for a full walk.
        """
        if self.__read_only:
            raise PermissionError("read-only ledger")
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
        item = json.loads(canonical(entry))
        item.setdefault("ts", self.__clock())
        if type(item["ts"]) is not int or item["ts"] < 0:
            raise ValueError("ts must be nonnegative integer nanoseconds")
        item.update(seq=self.__count, prev_hash=self.__head)
        item["hash"] = hashlib.sha256(canonical(item)).hexdigest()
        token = self.__keys._encrypt(canonical(item))
        if self.__path is not None:
            line = canonical({"item": token.decode("ascii")}) + b"\n"
            descriptor = os.open(self.__path,
                                 os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o666)
            try:
                remaining = memoryview(line)
                while remaining:
                    remaining = remaining[os.write(descriptor, remaining):]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.__size += len(line)
            self.__last_line = line
            self.__raw_hash.update(line)
        else:
            self.__tokens.append(token)
        self.__head = item["hash"]
        self.__count += 1
        self._index_item(self.__index, item)
        if item.get("kind") == "decision.handle":
            self.__decision_ids[item["seq"]] = f"decision-{self.__decision_count}"
            self.__decision_count += 1
        if item.get("kind") == "snapshot":
            self.checkpoint()
        return item["seq"]

    def verify(self) -> bool:
        """Detect changed bytes and headers, authenticating each unchanged ciphertext once.

        The verified prefix contains immutable bytes. Every pass compares its
        entire stored prefix byte-for-byte; only an identical prefix may reuse
        its authenticated head. New ciphertexts still undergo Fernet, sequence,
        previous-hash and canonical digest checks. Disk recovery can reuse its
        encrypted head only after checking the persisted prefix digest.
        """
        try:
            if self.__path is not None:
                size = self.__path.stat().st_size
                if size < self.__size or not self.__read_only and size != self.__size:
                    return False
                checked, hasher = self._scan_disk(self.__size)
                if checked["count"] != self.__count or checked["head"] != self.__head:
                    return False
                self.__checkpoint, self.__raw_hash = checked, hasher
                self.__index = self._copy_index(checked["index"])
                return True
            tokens = self.__tokens
            if len(tokens) != self.__count:
                return False
            prefix = self.__verified_tokens
            if len(tokens) < len(prefix) or any(
                token != tokens[seq] for seq, token in enumerate(prefix)
            ):
                return False
            previous = self.__verified_head
            for seq in range(len(prefix), len(tokens)):
                token = tokens[seq]
                item = json.loads(self.__keys._decrypt(token))
                digest = item.pop("hash")
                if item["seq"] != seq or item["prev_hash"] != previous:
                    return False
                if hashlib.sha256(canonical(item)).hexdigest() != digest:
                    return False
                previous = digest
            if previous != self.__head:
                return False
            self.__verified_tokens = tuple(tokens)
            self.__verified_head = previous
            return True
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
        if seq >= self.__count:
            raise IndexError(seq)
        if self.__path is None:
            return json.loads(self.__keys._decrypt(self.__tokens[seq]))
        # Post-mortem readers commonly walk consecutive items. Retain one byte
        # cursor, never all ciphertexts or a diary-sized seek index.
        current, offset = self.__read_cursor
        with self.__path.open("rb") as stream:
            if offset is None or seq < current:
                stream.readline()
                current = 0
            else:
                stream.seek(offset)
            while current < seq:
                if not stream.readline():
                    raise LedgerIntegrityError("incomplete ledger")
                current += 1
            line = stream.readline()
            self.__read_cursor = (seq + 1, stream.tell())
        item = json.loads(self.__keys._decrypt(self._token(line)))
        if item["seq"] != seq:
            raise LedgerIntegrityError("ledger sequence differs")
        return item

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
        if not params:
            index = self.__index
            if view == "wallet_series":
                return {"series": deepcopy(index["wallet_series"])}
            if view in ("spend_by_capability", "invocations_by_assembly", "action_frequencies"):
                key = {"spend_by_capability": "spend", "invocations_by_assembly": "invocations",
                       "action_frequencies": "actions"}[view]
                return {"spend" if key == "spend" else "counts": dict(sorted(index[key].items()))}
            return {"count": index["latency_count"], "total_ns": index["latency_total"],
                    "min_ns": index["latency_min"], "max_ns": index["latency_max"]}
        selected = (item for item in self._iter_items()
                    if item["ts"] >= since and (until is None or item["ts"] < until))
        if view == "wallet_series":
            return {
                "series": [
                    {"ts": item["ts"], "balance": item["balance_after"]}
                    for item in selected
                    if item.get("kind")
                    in ("wallet.initial", "wallet.commit", "wallet.drip", "wallet.settle",
                        "wallet.settle_uncertain")
                ]
            }
        if view == "spend_by_capability":
            choices = {
                item["handle"]: item["propensity"]["chosen"]
                for item in self._iter_items()
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
