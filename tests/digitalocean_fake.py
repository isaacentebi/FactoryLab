"""A fake DigitalOcean at the HTTP boundary, honest about field shapes and signs.

It answers the four documented reads and the one write with the JSON DigitalOcean
publishes (docs.digitalocean.com/reference/api/reference/): balances as two-decimal
strings where a negative balance is credit, prices as JSON numbers, sizes paginated
with ``meta.total``, actions with ``in-progress`` / ``completed`` / ``errored``. The
account moves only when a test says DigitalOcean billed, credited or rolled over.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

TOKEN = "dop_v1_" + "f" * 64

SIZES = (
    # slug, vcpus, memory MB, disk GB, monthly, hourly (DigitalOcean's own float text)
    ("s-1vcpu-1gb", 1, 1024, 25, "6", "0.00893"),
    ("s-1vcpu-2gb", 1, 2048, 50, "12", "0.01786"),
    ("s-2vcpu-2gb", 2, 2048, 60, "18", "0.02679"),
    ("s-2vcpu-4gb", 2, 4096, 80, "24", "0.03571"),
    ("s-4vcpu-8gb", 4, 8192, 160, "48", "0.07143"),
    ("s-8vcpu-16gb", 8, 16384, 320, "96", "0.14286"),
    ("g-2vcpu-8gb", 2, 8192, 25, "63", "0.09375"),
)


def _size(row, regions=("nyc3", "sfo3")) -> str:
    slug, vcpus, memory, disk, monthly, hourly = row
    # Built as JSON text so the prices are JSON numbers exactly as DigitalOcean writes them.
    return (f'{{"slug": "{slug}", "memory": {memory}, "vcpus": {vcpus}, "disk": {disk}, '
            f'"transfer": 2.0, "price_monthly": {monthly}, "price_hourly": {hourly}, '
            f'"regions": {json.dumps(list(regions))}, "available": true, '
            f'"description": "Basic"}}')


class FakeDigitalOcean:
    """One account and one droplet. ``calls`` records every request, headers included."""

    def __init__(self, *, droplet_id=599960972, size="s-1vcpu-2gb", region="nyc3",
                 credit="200.00", usage="0.00", token=TOKEN, per_page_cap=3):
        self.droplet_id = droplet_id
        self.size = size
        self.region = region
        self.token = token
        self.account = -Decimal(credit)  # negative: credit, as DigitalOcean shows it
        self.usage = Decimal(usage)
        self.generated = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
        self.sizes = {row[0]: row for row in SIZES}
        self.unavailable_regions: dict[str, tuple[str, ...]] = {"g-2vcpu-8gb": ("sfo3",)}
        self.locked = False
        self.status = "active"
        self.actions: dict[int, dict] = {}
        self.next_action = 1_000_000
        self.calls: list[dict] = []
        self.posts: list[dict] = []
        self.per_page_cap = per_page_cap
        # Faults: lose the answer to the next POST after acting on it; echo the
        # Authorization header in an error body; refuse every request.
        self.lose_post_answer = False
        self.echo_auth_on_error = False
        self.down = False
        self.observer = None  # called with the request before it is answered

    # ---- what the account does ---------------------------------------------------

    def tick(self, minutes=60):
        self.generated += timedelta(minutes=minutes)

    def bill(self, usd):
        """DigitalOcean accrues usage; ``month_to_date_balance`` rises with it."""
        self.usage += Decimal(usd)
        self.tick()

    def prepay(self, usd):
        """A prepayment or credit grant lowers the account balance (more credit)."""
        self.account -= Decimal(usd)
        self.tick()

    def rollover(self):
        """The first of the month: the invoice moves usage into the account balance."""
        self.account += self.usage
        self.usage = Decimal(0)
        self.tick()

    def finish(self, action_id, status="completed"):
        action = self.actions[action_id]
        action["status"] = status
        action["completed_at"] = self.generated.isoformat().replace("+00:00", "Z")
        if status == "completed":
            self.size = action["_size"]
        self.locked, self.status = False, "active"

    # ---- the HTTP boundary -------------------------------------------------------

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "body": body, "timeout": timeout})
        if self.observer is not None:
            self.observer(method, url)
        if self.down:
            raise ConnectionRefusedError("connection refused")
        if headers.get("Authorization") != f"Bearer {self.token}":
            text = ('{"id": "unauthorized", "message": "Unable to authenticate you", '
                    f'"echo": "{headers.get("Authorization")}"}}'
                    if self.echo_auth_on_error else
                    '{"id": "unauthorized", "message": "Unable to authenticate you"}')
            return 401, text.encode()
        path = url.split("api.digitalocean.com", 1)[-1]
        if method == "GET" and path == "/v2/customers/my/balance":
            return 200, self._balance()
        if method == "GET" and path == f"/v2/droplets/{self.droplet_id}":
            return 200, self._droplet()
        if method == "GET" and path.startswith("/v2/sizes"):
            return 200, self._sizes(path)
        if method == "POST" and path == f"/v2/droplets/{self.droplet_id}/actions":
            return self._resize(json.loads(body))
        if method == "GET" and path.startswith("/v2/actions/"):
            action = self.actions.get(int(path.rsplit("/", 1)[-1]))
            if action is None:
                return 404, b'{"id": "not_found", "message": "not found"}'
            return 200, json.dumps({"action": self._public(action)}).encode()
        return 404, b'{"id": "not_found", "message": "The resource was not found."}'

    def _balance(self) -> bytes:
        return json.dumps({
            "month_to_date_balance": f"{self.account + self.usage:.2f}",
            "account_balance": f"{self.account:.2f}",
            "month_to_date_usage": f"{self.usage:.2f}",
            "generated_at": self.generated.isoformat().replace("+00:00", "Z"),
        }).encode()

    def _droplet(self) -> bytes:
        row = self.sizes[self.size]
        return (f'{{"droplet": {{"id": {self.droplet_id}, "name": "superdarkfactory", '
                f'"memory": {row[2]}, "vcpus": {row[1]}, "disk": {row[3]}, '
                f'"locked": {json.dumps(self.locked)}, "status": "{self.status}", '
                f'"size_slug": "{self.size}", "size": {_size(row)}, '
                f'"region": {{"slug": "{self.region}", "name": "New York 3"}}}}}}').encode()

    def _sizes(self, path) -> bytes:
        query = dict(part.split("=") for part in path.split("?", 1)[1].split("&"))
        per_page = min(int(query.get("per_page", 20)), self.per_page_cap)
        page = int(query.get("page", 1))
        rows = list(self.sizes.values())[(page - 1) * per_page: page * per_page]
        body = ", ".join(_size(row, self.unavailable_regions.get(row[0], ("nyc3", "sfo3")))
                         for row in rows)
        return (f'{{"sizes": [{body}], "links": {{}}, '
                f'"meta": {{"total": {len(self.sizes)}}}}}').encode()

    def _resize(self, payload):
        self.posts.append(payload)
        if payload.get("type") != "resize" or payload.get("size") not in self.sizes:
            return 422, b'{"id": "unprocessable_entity", "message": "invalid size"}'
        if self.locked:
            return 422, b'{"id": "unprocessable_entity", "message": "Droplet is locked"}'
        action_id = self.next_action
        self.next_action += 1
        self.actions[action_id] = {
            "id": action_id, "status": "in-progress", "type": "resize",
            "started_at": self.generated.isoformat().replace("+00:00", "Z"),
            "completed_at": None, "resource_id": self.droplet_id,
            "resource_type": "droplet", "region_slug": self.region,
            "_size": payload["size"], "_disk": payload.get("disk")}
        # DigitalOcean powers the droplet down and locks it while it resizes.
        self.locked, self.status = True, "off"
        if self.lose_post_answer:
            self.lose_post_answer = False
            raise ConnectionResetError("connection reset after the request was sent")
        return 201, json.dumps({"action": self._public(self.actions[action_id])}).encode()

    @staticmethod
    def _public(action):
        return {k: v for k, v in action.items() if not k.startswith("_")}
