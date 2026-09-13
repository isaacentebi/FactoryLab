"""Bounded public notes retain contents and pay storage rent from their writer's compute."""

from copy import deepcopy
from dataclasses import dataclass


@dataclass(frozen=True)
class NotesSpec:
    """The world's notebook capacity and exact byte-window price are fixed at launch."""

    max_keys: int = 128
    max_bytes: int = 262144
    byte_window_micro: int = 1

    def __post_init__(self):
        for name in ("max_keys", "max_bytes", "byte_window_micro"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"notes.{name} must be a positive integer")


def specs(bounds: NotesSpec) -> dict:
    """Both population tools expose bounded keys and a price determined before dispatch."""
    key = {"type": "string", "minLength": 1, "maxLength": 128}
    return {f"note.{name}": {
        "id": f"note.{name}", "kind": "note", "description": description,
        "args_schema": {"type": "object", "properties": properties,
                        "required": list(properties), "additionalProperties": False},
        "price_micro_per_call": bounds.byte_window_micro,
    } for name, properties, description in (
        ("put", {"key": key, "text": {"type": "string", "maxLength": bounds.max_bytes}},
         "Register or overwrite public text under a key; UTF-8 bytes are charged per window."),
        ("get", {"key": key},
         "Read public text, paying returned bytes and any outstanding storage rent."))}


def counts(entries: dict) -> dict:
    """Wake metadata contains only notebook counts, never keys, authors or text."""
    return {"keys": len(entries), "bytes": sum(entry["bytes"] for entry in entries.values())}


def prepare(entries: dict, bounds: NotesSpec, tool: str, args: dict,
            window: int) -> tuple[dict, int]:
    """A detached successor and exact price are validated before any debit or overwrite."""
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
    retained = rent_bytes(previous, window) if previous else 0
    cost = (size + retained) * bounds.byte_window_micro
    entry = {"text": text, "bytes": size, "paid_window": window,
             "version": (previous["version"] if previous else 0) + (tool == "note.put")}
    return deepcopy(entry), cost


def rent_bytes(entry: dict, window: int) -> int:
    """A paid byte-window is charged once, including after an unaffordable boundary."""
    return max(0, window - entry["paid_window"]) * entry["bytes"]


def charge_window(rt) -> None:
    """Every retained note pays each elapsed window or retains its unpaid rent and text."""
    from factorylab.world.metering import Infeasible

    for key, entry in rt.notes.items():
        price = rent_bytes(entry, rt.window.index) * rt.m.notes.byte_window_micro
        if not price:
            continue
        try:
            paid = rt.meter.run(handle=entry["handle"], reason="tool:note.storage",
                                ceiling=price, execute=lambda: None,
                                cost_of=lambda _, price=price: price)
        except Infeasible:
            rt.ledger.append({"kind": "note.rent_due", "key": key, "cost": price,
                              "window": rt.window.index, "handle": entry["handle"],
                              "ts": rt.clock.now_ns})
            continue
        rt.ledger.append({"kind": "note.rent", "key": key, "cost": paid.cost,
                          "window": rt.window.index, "handle": entry["handle"],
                          "ts": rt.clock.now_ns})
        entry["paid_window"] = rt.window.index


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
