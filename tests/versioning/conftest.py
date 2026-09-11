import pytest


@pytest.fixture
def diary():
    """Synthetic windows use exactly the runtime's persisted item shapes."""

    def make(rows):
        items = []
        for i, row in enumerate(rows):
            items.extend(row.get("before", []))
            for card, region in row.get("regions", {}).items():
                items.append({"kind": "price.region", "card_id": card, "region": region})
            for channel in ("verdict", "conformity", "fast", "consequence", "exposure"):
                if row.get(channel) is not None:
                    items.append(
                        {
                            "kind": "decision.settle",
                            "return": {
                                "channel": channel,
                                "score": row[channel],
                                "status": "settled",
                            },
                        }
                    )
            for _ in range(row.get("registrations", 1)):
                items.append({"kind": "event", "event": {"kind": "Registered", "payload": {}}})
            if "balance" in row:
                items.append({"kind": "wallet.settle", "balance_after": row["balance"]})
            items.extend(row.get("extra", []))
            items.append(
                {
                    "kind": "price.window",
                    "window": i + 1,
                    "window_end_event": (i + 1) * 120,
                    "values": row.get("cards", {}),
                }
            )
        return [dict(item, seq=i) for i, item in enumerate(items)]

    return make
