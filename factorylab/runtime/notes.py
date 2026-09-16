"""Bounded public notes retain contents and pay storage rent from their writer's compute.

Rent is by byte-time: ``bytes × elapsed_ns × rate``, accrued at each reserve
window boundary from the moment a note was written. The rate is the manifest's
``notes.micro_per_byte_day`` and the arithmetic is exact: whatever fraction of
a micro-USD a window leaves over is carried on the note, so collecting rent
often can never round it up and collecting it rarely can never round it down.

Rent is a liability of the decision that holds the note, resumable with the
notebook itself: an unpaid accrual stays outstanding until it is paid, and a
paid one is attributed to that decision's cost, not merely subtracted from the
wallet.

There is no longer a transfer toll (edition 3, C4). ``byte_window_micro`` used to
price every byte moved in a put or a get, which made a 4 KiB read cost about
$0.0041 before the model had consumed one character of it — more than a cheap
model call, and a charge with no resource behind it, since moving bytes inside
one process costs the factory nothing. Byte-time rent stays: storage is a real
resource and a note nobody will pay to keep should go. What remains of
``byte_window_micro`` is the flat per-call price of a notebook tool, so a world's
manifest still bounds how often the notebook is touched. Remembering must not be
made more expensive than producing another unsupported paragraph.
"""

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction

NS_PER_DAY = 24 * 3_600 * 1_000_000_000
#: Rows one ``note.list`` page returns before a cursor is needed.
PAGE = 50
#: 262144 bytes at 0.04 micro-USD per byte-day is 10485.76 micro-USD, about a cent a day.
DEFAULT_MICRO_PER_BYTE_DAY = "0.04"


@dataclass(frozen=True)
class NotesSpec:
    """The world's notebook capacity, flat call price and byte-day rent are fixed at launch."""

    max_keys: int = 128
    max_bytes: int = 262144
    byte_window_micro: int = 1
    micro_per_byte_day: str = DEFAULT_MICRO_PER_BYTE_DAY

    def __post_init__(self):
        for name in ("max_keys", "max_bytes", "byte_window_micro"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"notes.{name} must be a positive integer")
        rate = self.micro_per_byte_day
        if type(rate) not in (str, int):
            raise ValueError("notes.micro_per_byte_day must be exact decimal text or an integer")
        try:
            value = Decimal(rate)
        except InvalidOperation:
            raise ValueError("notes.micro_per_byte_day must be exact decimal text") from None
        if not value.is_finite() or value <= 0:
            raise ValueError("notes.micro_per_byte_day must be a positive finite rate")
        object.__setattr__(self, "micro_per_byte_day", format(value.normalize(), "f"))

    def rate(self) -> tuple[int, int]:
        """Return the rent rate as an exact ratio of micro-USD per byte-nanosecond."""
        per_day = Fraction(Decimal(self.micro_per_byte_day))
        per_ns = per_day / NS_PER_DAY
        return per_ns.numerator, per_ns.denominator


def specs(bounds: NotesSpec) -> dict:
    """The notebook's paying tools expose bounded keys and a flat price before dispatch."""
    key = {"type": "string", "minLength": 1, "maxLength": 128}
    return {f"note.{name}": {
        "id": f"note.{name}", "kind": "note", "description": description,
        "args_schema": {"type": "object", "properties": properties,
                        "required": list(properties), "additionalProperties": False},
        "price_micro_per_call": bounds.byte_window_micro,
    } for name, properties, description in (
        ("put", {"key": key, "text": {"type": "string", "maxLength": bounds.max_bytes}},
         "Register or overwrite public text under a key; the retained text pays rent by "
         "byte-time and no per-byte transfer charge applies."),
        ("get", {"key": key},
         "Read public text, paying any outstanding storage rent on the key."))}


def list_spec() -> dict:
    """The notebook's index tool, free like ``artifact.get``.

    A directory nobody can afford to read is not a directory, and the index
    carries no text — only what a reader needs to decide which key is worth
    paying that key's rent for. It is registered beside ``artifact.list`` rather
    than with the paying notebook tools, so a world that predates the shared
    directory grows it on resume without a manifest change.
    """
    return {
        "id": "note.list", "kind": "note",
        "description": f"Index the notebook: up to {PAGE} rows of key, bytes, version, "
        "owner seat and the window last written, newest first, with a cursor for the next "
        "page. Notes are public by construction; the index carries no text, so a reader "
        "need not already know a key.",
        "args_schema": {"type": "object",
                        "properties": {"cursor": {"type": "string", "maxLength": 128}},
                        "additionalProperties": False,
                        # Every published tool carries examples its own schema accepts (B1).
                        "examples": [{}, {"cursor": "shared-plan"}]},
        "price_micro_per_call": 0,
    }


def index(entries: dict, cursor: str | None = None) -> dict:
    """One page of the notebook's index: keys and metadata, never text.

    Guarantees the paging is total and stable: rows are ordered by the window
    last written and then by key, a cursor names the row to resume after, and the
    page that ends the listing returns ``next_cursor`` of ``None``. An unknown
    cursor yields an empty final page rather than the first one again, so a
    caller can never loop. Nothing here discloses a note's contents: a reader who
    wants the text calls ``note.get`` and pays that key's outstanding rent.
    """
    rows = sorted(
        ({"key": key, "title": key, "type": "note", "bytes": entry["bytes"],
          "version": entry["version"], "owner": entry.get("owner"),
          "updated_window": entry.get("window"), "public": True}
         for key, entry in entries.items()),
        key=lambda row: (-(row["updated_window"] or 0), row["key"]))
    start = 0
    if cursor:
        keys = [row["key"] for row in rows]
        start = keys.index(cursor) + 1 if cursor in keys else len(rows)
    page = rows[start:start + PAGE]
    following = start + len(page)
    return {"items": page, "count": len(rows),
            "next_cursor": page[-1]["key"] if page and following < len(rows) else None}


def counts(entries: dict) -> dict:
    """Wake metadata contains only notebook counts, never keys, authors or text."""
    return {"keys": len(entries), "bytes": sum(entry["bytes"] for entry in entries.values())}


def prepare(entries: dict, bounds: NotesSpec, tool: str, args: dict,
            window: int) -> tuple[dict, int]:
    """A detached successor and exact price are validated before any debit or overwrite.

    The price is the notebook tool's flat call price plus whatever rent the key
    already owes; it depends on nothing but the notebook and the request, so the
    dispatcher's ceiling check and the paid call agree exactly. Nothing is
    charged per byte moved: the bytes cost the factory nothing to move, and rent
    already prices the bytes that stay.
    """
    if tool not in ("note.put", "note.get"):
        raise ValueError("unknown note tool")
    required = {"key", "text"} if tool == "note.put" else {"key"}
    if not isinstance(args, dict) or set(args) != required:
        raise ValueError("invalid note arguments")
    key = args["key"]
    if (not isinstance(key, str) or not key or len(key.encode("utf-8")) > 128
            or any(not c.isprintable() for c in key)):
        raise ValueError("note key must contain 1..128 printable UTF-8 bytes")
    previous = entries.get(key)
    if tool == "note.get" and previous is None:
        raise ValueError("unknown note key")
    text = args.get("text") if tool == "note.put" else previous["text"]
    if not isinstance(text, str):
        raise ValueError("note text must be a string")
    size = len(key.encode("utf-8")) + len(text.encode("utf-8"))
    if len(entries) + (previous is None) > bounds.max_keys:
        raise ValueError("notes.max_keys exceeded")
    if counts(entries)["bytes"] - (previous["bytes"] if previous else 0) + size > bounds.max_bytes:
        raise ValueError("notes.max_bytes exceeded")
    owed = previous.get("rent_due", 0) if previous else 0
    cost = bounds.byte_window_micro + owed
    entry = {"text": text, "bytes": size, "window": window,
             "version": (previous["version"] if previous else 0) + (tool == "note.put"),
             # Accrual continues from the last boundary the key was accounted to: an
             # overwrite inherits the open interval, so no rent is forgiven by rewriting.
             "rent_ns": previous.get("rent_ns") if previous else None,
             "rent_carry": previous.get("rent_carry", 0) if previous else 0,
             "rent_due": 0,
             # The seat that wrote the key, so the directory can say who owns a row.
             # An overwrite by another seat takes ownership with the liability.
             "owner": previous.get("owner") if previous else None}
    return deepcopy(entry), cost


def accrue(entry: dict, now_ns: int, bounds: NotesSpec) -> tuple[int, int]:
    """Return (micro-USD newly due, remainder to carry) for the interval since the last accrual."""
    since = entry.get("rent_ns")
    if since is None:
        return 0, entry.get("rent_carry", 0)
    numerator, denominator = bounds.rate()
    byte_ns = entry.get("rent_byte_ns", 0) + entry["bytes"] * max(0, now_ns - since)
    owed = byte_ns * numerator + entry.get("rent_carry", 0)
    return divmod(owed, denominator)


def charge_window(rt) -> None:
    """Every retained note pays the rent its bytes accrued, or retains its unpaid rent and text.

    A paid charge is a scored liability of the decision that holds the note, not
    only a wallet debit: it enters that decision's cost contribution for this
    window and, while its consequence outcome is still open, that outcome's cost.
    Unpaid rent stays outstanding, so nothing is forgiven by an unaffordable
    boundary and nothing is charged twice: the accrued interval is closed and
    its amount carried as due.
    """
    from factorylab.world.metering import Infeasible

    now_ns = rt.clock.now_ns
    for key, entry in rt.notes.items():
        micro, carry = accrue(entry, now_ns, rt.m.notes)
        price = entry.get("rent_due", 0) + micro
        entry["rent_ns"], entry["rent_carry"] = now_ns, carry
        if not price:
            continue
        try:
            paid = rt.meter.run(handle=entry["handle"], reason="tool:note.storage",
                                ceiling=price, execute=lambda: None,
                                cost_of=lambda _, price=price: price)
        except Infeasible:
            entry["rent_due"] = price
            rt.ledger.append({"kind": "note.rent_due", "key": key, "cost": price,
                              "window": rt.window.index, "handle": entry["handle"],
                              "ts": now_ns})
            continue
        rt.ledger.append({"kind": "note.rent", "key": key, "cost": paid.cost,
                          "window": rt.window.index, "handle": entry["handle"],
                          "ts": now_ns})
        rt._charge_storage(entry["handle"], paid.cost)
        entry["rent_due"] = 0


def run(rt, action_id: str, handle: str, tool: str, args: dict, *, ceiling: int | None = None):
    """No note is changed or disclosed until its caller's own compute pays the exact cost."""
    from factorylab.world.metering import Infeasible

    entries = rt.notes
    try:
        entry, price = prepare(entries, rt.m.notes, tool, args, rt.window.index)
        available = rt.wallet.available_for(handle, f"tool:{tool}")
        if price > available or ceiling is not None and price > ceiling:
            raise ValueError("note exceeds caller available compute")
        entry["handle"] = handle if tool == "note.put" else entries[args["key"]]["handle"]
        if tool == "note.put":
            entry["owner"] = action_id
        if entry["rent_ns"] is None:
            entry["rent_ns"] = rt.clock.now_ns  # a new key accrues from the moment it is written
        def read():
            return rt.ledger.call("note.read", lambda key: deepcopy(entries[key]),
                                  (args["key"],), {}, deterministic=True)

        paid = rt.meter.run(handle=handle, reason=f"tool:{tool}", ceiling=price,
                            execute=read if tool == "note.get" else lambda: None,
                            cost_of=lambda _: price)
    except (ValueError, Infeasible) as exc:
        return {"error": str(exc)}, 0
    rt.ledger.append({"kind": tool, "key": args["key"], "handle": handle,
                      "assembly_id": action_id, "cost": paid.cost, "window": rt.window.index,
                      "version": entry["version"], "bytes": entry["bytes"],
                      **({"text": entry["text"]} if tool == "note.put" else {}),
                      "ts": rt.clock.now_ns})
    entries[args["key"]] = entry
    return {"key": args["key"], "text": entry["text"], "version": entry["version"]}, paid.cost
