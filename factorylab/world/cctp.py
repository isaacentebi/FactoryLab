"""CCTP V2 proves immutable burn fields and actual mint, including deducted fees.

Sources checked 2026-09-11:
https://developers.circle.com/cctp/references/technical-guide
https://developers.circle.com/cctp/references/contract-interfaces
https://developers.circle.com/cctp/references/contract-addresses
https://developers.circle.com/cctp/concepts/fees
"""

from __future__ import annotations

from factorylab.world.evm import (
    EVM,
    Pending,
    RailError,
    calldata,
    decode_log,
    event_topic,
    word_address,
)
from factorylab.world.x402 import Transport, http_request


class CCTP:
    """Attestations cannot change a burn's recipient, token, amount, domains, sender or fee cap."""

    def __init__(self, *, testnet: bool, transport: Transport = http_request):
        self.testnet = testnet
        self.url = (
            "https://iris-api-sandbox.circle.com" if testnet else "https://iris-api.circle.com"
        )
        self.transport = transport

    def validate_pair(self, source: EVM, destination: EVM) -> None:
        allowed = {998, 84532} if self.testnet else {999, 8453}
        if {source.chain.id, destination.chain.id} != allowed:
            raise RailError("unsupported or mixed mainnet/testnet CCTP pair")

    def burn(
        self, source: EVM, destination: EVM, amount: int, remaining: int, max_fee_micro: int
    ) -> dict:
        self.validate_pair(source, destination)
        if (
            type(amount) is not int
            or type(max_fee_micro) is not int
            or not 0 <= max_fee_micro < amount
        ):
            raise RailError("burn must exceed its nonnegative integer fee cap")
        if source.balance(source.chain.usdc) < amount:
            raise RailError("insufficient CCTP USDC")
        # Circle's pinned Base deployments have no fee switch or this accessor.
        # Calling it there reverts. This is a chain-specific compatibility rule,
        # never a catch-all that treats an unavailable fee quote as free.
        minimum = 0
        if source.chain.id not in (8453, 84532):
            minimum = int.from_bytes(source.read(
                source.chain.messenger,
                calldata("getMinFeeAmount(uint256)", ["uint256"], [amount]),
            ))
        if minimum > max_fee_micro:
            raise RailError("CCTP standard transfer fee exceeds the declared cap")
        data = calldata(
            "depositForBurn(uint256,uint32,bytes32,address,bytes32,uint256,uint32)",
            ["uint256", "uint32", "bytes32", "address", "bytes32", "uint256", "uint32"],
            [
                amount,
                destination.chain.domain,
                word_address(destination.account.address),
                source.chain.usdc,
                bytes(32),
                max_fee_micro,
                2000,
            ],
        )
        return {
            **source.prepare(source.chain.messenger, data, gas_remaining_wei=remaining),
            "cctp_max_fee_micro": max_fee_micro,
            "cctp_min_finality": 2000,
        }

    def message(
        self,
        receipt: dict,
        source: EVM,
        destination: EVM,
        amount: int,
        *,
        sender: str,
        max_fee_micro: int,
        min_finality: int,
        hook: bytes = b"",
    ) -> bytes:
        found = [
            decode_log(log, ["bytes"])[0]
            for log in receipt.get("logs", [])
            if log["address"].lower() == source.chain.transmitter.lower()
            and log.get("topics") == [event_topic("MessageSent(bytes)")]
            and not log.get("removed", False)
        ]
        if len(found) != 1:
            raise RailError("burn receipt must contain exactly one CCTP MessageSent")
        self.validate_message(
            found[0],
            source,
            destination,
            amount,
            sender=sender,
            max_fee_micro=max_fee_micro,
            min_finality=min_finality,
            hook=hook,
        )
        return found[0]

    def validate_message(
        self,
        message: bytes,
        source: EVM,
        destination: EVM,
        amount: int,
        *,
        sender: str,
        max_fee_micro: int,
        min_finality: int,
        hook: bytes = b"",
    ) -> None:
        """Check V2's entire immutable header and burn body before accepting an attestation."""
        self.validate_pair(source, destination)
        if len(message) != 376 + len(hook):
            raise RailError("unexpected CCTP V2 message length")
        checks = (
            int.from_bytes(message[0:4]) == 1,
            int.from_bytes(message[4:8]) == source.chain.domain,
            int.from_bytes(message[8:12]) == destination.chain.domain,
            message[44:76] == word_address(source.chain.messenger),
            message[76:108] == word_address(destination.chain.messenger),
            message[108:140] == bytes(32),
            int.from_bytes(message[140:144]) == min_finality,
            int.from_bytes(message[148:152]) == 1,
            message[152:184] == word_address(source.chain.usdc),
            message[184:216] == word_address(destination.account.address),
            int.from_bytes(message[216:248]) == amount,
            message[248:280] == word_address(sender),
            int.from_bytes(message[280:312]) == max_fee_micro,
            message[376:] == hook,
        )
        if not all(checks):
            raise RailError("CCTP message does not match the intended transfer")

    @staticmethod
    def immutable(message: bytes) -> bytes:
        """Only Circle's nonce, executed finality, fee and expiry may vary after a burn."""
        return message[:12] + message[44:144] + message[148:312] + message[376:]

    def expected_message(self, source: EVM, destination: EVM, amount: int, *,
                         sender: str, max_fee_micro: int, min_finality: int,
                         hook: bytes = b"") -> bytes:
        """Construct every immutable V2 field from the recorded intent, never from Iris output."""
        self.validate_pair(source, destination)
        return b"".join((
            (1).to_bytes(4), source.chain.domain.to_bytes(4), destination.chain.domain.to_bytes(4),
            bytes(32), word_address(source.chain.messenger),
            word_address(destination.chain.messenger),
            bytes(32), min_finality.to_bytes(4), bytes(4), (1).to_bytes(4),
            word_address(source.chain.usdc), word_address(destination.account.address),
            amount.to_bytes(32), word_address(sender), max_fee_micro.to_bytes(32),
            bytes(64), hook,
        ))

    def prove_system_burn(self, source: EVM, destination: EVM, tx_hash: str,
                          expected: bytes) -> dict:
        """Only a matching attestation accepted by the pinned destination contract proves a burn.

        HyperEVM's official node omits system transaction receipts. The separate
        system-call proof plus Circle's cryptographically checked message replaces
        that missing receipt. eth_call performs no mint or paid write.

        A forwarded withdrawal is delivered by Circle within seconds of the
        attestation, and the transmitter then reverts the dry run with "Nonce
        already used" for good. Its own consumed-nonce record is the stronger
        proof, so it is read first; the dry run remains the check for a message
        nobody has delivered yet. Neither says the reserve was credited: only the
        mint step's MessageReceived log and exact USDC Transfer do that.
        """
        burn = {"tx_hash": tx_hash, "message": "0x" + expected.hex()}
        message, proof, _ = self.attestation(source, destination, burn)
        if not self.consumed(destination, message):
            data = calldata("receiveMessage(bytes,bytes)", ["bytes", "bytes"], [message, proof])
            if destination.read(destination.chain.transmitter, data) != (1).to_bytes(32):
                raise RailError("destination contract did not validate the Circle attestation")
        return {"tx_hash": tx_hash, "message": "0x" + message.hex()}

    @staticmethod
    def consumed(destination: EVM, message: bytes) -> bool:
        """True when the pinned transmitter has already accepted this exact CCTP nonce."""
        return int.from_bytes(destination.read(
            destination.chain.transmitter,
            calldata("usedNonces(bytes32)", ["bytes32"], [message[12:44]]),
        )) == 1

    def attestation(self, source: EVM, destination: EVM, burn: dict) -> tuple[bytes, bytes, int]:
        """Match Iris output to the confirmed transaction's message before permitting a mint."""
        self.validate_pair(source, destination)
        tx_hash = burn["tx_hash"]
        if not isinstance(tx_hash, str) or len(tx_hash) != 66:
            raise RailError("invalid CCTP burn transaction reference")
        url = f"{self.url}/v2/messages/{source.chain.domain}?transactionHash={tx_hash}"
        try:
            response = self.transport("GET", url, None, {"User-Agent": "FactoryLab/0.4"})
        except Exception:
            raise Pending("Circle attestation service unavailable") from None
        if response.status in (404, 429, 500, 502, 503, 504):
            raise Pending("Circle attestation is pending")
        if response.status != 200:
            raise RailError("Circle attestation request was rejected")
        original = bytes.fromhex(burn["message"].removeprefix("0x"))
        matches = []
        try:
            for row in response.body.get("messages", []):
                if row.get("status") != "complete":
                    continue
                candidate = bytes.fromhex(row["message"].removeprefix("0x"))
                if len(candidate) == len(original) and self.immutable(candidate) == self.immutable(
                    original
                ):
                    proof = bytes.fromhex(row["attestation"].removeprefix("0x"))
                    matches.append((candidate, proof))
        except (KeyError, ValueError, AttributeError, TypeError):
            raise RailError("invalid Circle attestation response") from None
        if not matches:
            raise Pending("matching Circle attestation is not yet available")
        if len(matches) != 1:
            raise RailError("ambiguous CCTP attestation")
        message, proof = matches[0]
        if not proof or len(proof) % 65 or len(proof) > 6500:
            raise RailError("invalid Circle attestation size")
        executed, fee = int.from_bytes(message[144:148]), int.from_bytes(message[312:344])
        if executed < int.from_bytes(original[140:144]) or fee > int.from_bytes(original[280:312]):
            raise RailError("attestation finality or fee violates the burn")
        expiry = int.from_bytes(message[344:376])
        if expiry and expiry <= destination.block():
            self.transport(
                "POST",
                self.url + "/v2/reattest/0x" + message[12:44].hex(),
                {},
                {"User-Agent": "FactoryLab/0.4"},
            )
            raise Pending("expired CCTP attestation requested again")
        return message, proof, fee

    def mint(self, source: EVM, destination: EVM, burn: dict, remaining: int) -> dict:
        message, proof, fee = self.attestation(source, destination, burn)
        data = calldata("receiveMessage(bytes,bytes)", ["bytes", "bytes"], [message, proof])
        ref = destination.prepare(destination.chain.transmitter, data, gas_remaining_wei=remaining)
        return {**ref, "cctp_fee_micro": fee, "cctp_nonce": "0x" + message[12:44].hex()}

    @staticmethod
    def minted(receipt: dict, destination: EVM, amount: int) -> bool:
        """Require canonical-USDC mint credit to the reserve, not just a successful call."""
        topics = [
            event_topic("Transfer(address,address,uint256)"),
            "0x" + bytes(32).hex(),
            "0x" + word_address(destination.account.address).hex(),
        ]
        return any(
            log["address"].lower() == destination.chain.usdc.lower()
            and [t.lower() for t in log["topics"]] == [t.lower() for t in topics]
            and int(log["data"], 16) == amount
            and not log.get("removed", False)
            for log in receipt.get("logs", [])
        )
