"""A signed norm edition: the one input through which the norm house writes to a living world.

Essay II.IV.a: a board of humans or extrafactory agents votes "on a more abstract and
primary array of norms"; that "top congressional house is 'read-only' from the
perspective of the factory, though the factory is expected to testify within the
assembly". The write permission is a launch cast: the manifest's
``[norm_house] signer``. A norm edition carries norms and nothing else, so neither
money nor kernel physics is reachable through it. The runtime reads it only at a
governance boundary (``runtime/governance.py``), after the seated committee's
testimony, and applies it as ``edition + 1``.

The file is JSON::

    {"format": "factorylab.norm-edition/1", "world": <manifest name>,
     "manifest_sha256": <the world's manifest hash>, "sequence": <1, 2, ...>,
     "norms": [<name> | {"id": <name>, "definition": <text>}, ...],
     "signer": <0x address>, "signature": <0x EIP-191 signature of the digest>}

The digest is the sha256 of the canonical JSON of every field but ``signer`` and
``signature``; the signature is an EIP-191 ``personal_sign`` of that hex digest. A
sequence is applied once, in order, so an edition cannot be replayed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from factorylab.charter.charter import Norm

FORMAT = "factorylab.norm-edition/1"
SIGNED_FIELDS = ("format", "world", "manifest_sha256", "sequence", "norms")
FIELDS = (*SIGNED_FIELDS, "signer", "signature")
MAX_BYTES = 256 * 1024


def _norms_raw(norms) -> list:
    out = []
    for raw in norms:
        norm = Norm.parse(raw)
        out.append(norm.as_dict() if norm.definition else str(norm))
    return out


def digest(body: dict) -> str:
    """The sha256 of the signed fields' canonical JSON; the signature covers exactly this."""
    signed = {name: body[name] for name in SIGNED_FIELDS}
    encoded = json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build(*, world: str, manifest_sha256: str, sequence: int, norms, private_key: str) -> dict:
    """Return a signed norm edition. The key signs and is never written or returned."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    if type(sequence) is not int or sequence < 1:
        raise ValueError("sequence must be a positive integer")
    body: dict[str, Any] = {"format": FORMAT, "world": world, "manifest_sha256": manifest_sha256,
                            "sequence": sequence, "norms": _norms_raw(norms)}
    if not body["norms"]:
        raise ValueError("a norm edition needs at least one norm")
    account = Account.from_key(private_key)
    signed = account.sign_message(encode_defunct(text=digest(body)))
    signature = "0x" + signed.signature.hex().removeprefix("0x")
    return {**body, "signer": account.address.lower(), "signature": signature}


def verify(body: Any, *, signer: str | None, world: str, manifest_sha256: str,
           sequence: int) -> tuple[tuple[Norm, ...], str]:
    """Return the edition's norms and digest, or raise ``ValueError`` naming why it is refused.

    Refused: no signer cast in the manifest; any field other than ``FIELDS``; a
    different world, manifest or sequence; a malformed norm list; a signature that
    does not recover to the manifest's signer.
    """
    from eth_account import Account
    from eth_account.messages import encode_defunct

    if signer is None:
        raise ValueError("the manifest casts no norm_house.signer: no norm edition is possible")
    if not isinstance(body, dict) or set(body) != set(FIELDS):
        raise ValueError(f"a norm edition has exactly the fields {', '.join(FIELDS)}")
    if body["format"] != FORMAT:
        raise ValueError(f"format must be {FORMAT}")
    if body["world"] != world or body["manifest_sha256"] != manifest_sha256:
        raise ValueError("the norm edition names another world")
    if body["sequence"] != sequence or type(body["sequence"]) is not int:
        raise ValueError(f"the next norm edition is sequence {sequence}")
    if not isinstance(body["norms"], list) or not body["norms"]:
        raise ValueError("norms must be a nonempty list")
    try:
        norms = tuple(Norm.parse(raw) for raw in body["norms"])
    except ValueError as exc:
        raise ValueError(f"norms: {exc}") from None
    names = [str(n) for n in norms]
    if len(set(names)) != len(names):
        raise ValueError("norms must be unique")
    value = digest(body)
    try:
        recovered = Account.recover_message(encode_defunct(text=value),
                                            signature=body["signature"])
    except Exception:  # noqa: BLE001 - any undecodable signature is simply invalid
        raise ValueError("the signature is invalid") from None
    if str(recovered).lower() != signer.lower() or str(body["signer"]).lower() != signer.lower():
        raise ValueError("the signature is not the manifest's norm_house.signer")
    return norms, value


def inbox_path(ledger_path: str | Path, sequence: int) -> Path:
    """Where the CLI writes, and the runtime looks for, norm edition ``sequence``."""
    ledger = Path(ledger_path)
    return ledger.with_name(ledger.name + ".norms") / f"{sequence}.json"


class NormInbox:
    """The files beside a world's ledger that the norm house writes into.

    ``lookup`` is a read: the runtime calls it through the recovery journal, so a
    resumed world reads what the original run read at the same boundary.
    """

    def __init__(self, ledger_path: str | Path | None) -> None:
        self.ledger_path = ledger_path

    def lookup(self, sequence: int) -> dict | None:
        """The parsed file for ``sequence``; None when absent; an error record when unreadable."""
        if self.ledger_path is None:
            return None
        path = inbox_path(self.ledger_path, sequence)
        if not path.is_file():
            return None
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            return {"unreadable": f"larger than {MAX_BYTES} bytes"}
        try:
            return {"body": json.loads(raw)}
        except ValueError:
            return {"unreadable": "not JSON"}
