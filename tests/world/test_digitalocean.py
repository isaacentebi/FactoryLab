"""The DigitalOcean adapter reads exactly, publishes no prose and redacts its token.

Every test runs against ``tests.digitalocean_fake`` at the HTTP boundary; nothing
here reaches the network.
"""

import json
from decimal import Decimal

import pytest

from factorylab.world.digitalocean import TOKEN_ENV, DigitalOceanClient, DigitalOceanError
from tests.digitalocean_fake import METADATA, TOKEN, FakeDigitalOcean


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    return TOKEN


def _client(fake, **kwargs):
    return DigitalOceanClient(http=fake, **kwargs)


def _answer(balance, account, usage, generated_at="2026-09-23T12:00:00Z"):
    def http(method, url, headers, body, timeout):
        return 200, json.dumps({"month_to_date_balance": balance, "account_balance": account,
                                "month_to_date_usage": usage,
                                "generated_at": generated_at}).encode()
    return http


def test_balance_is_exact_and_a_negative_balance_is_credit(token):
    fake = FakeDigitalOcean(credit="200.00", usage="11.21")
    read = _client(fake).balance()
    assert read == {"month_to_date_balance_micro": -188_790_000,
                    "account_balance_micro": -200_000_000,
                    "month_to_date_usage_micro": 11_210_000,
                    "credit_micro": 188_790_000, "generated_at": "2026-09-23T12:00:00Z"}
    call = fake.calls[-1]
    assert call["method"] == "GET" and call["url"].endswith("/v2/customers/my/balance")
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
    # DigitalOcean's documented example: a positive balance is an amount owed.
    owed = DigitalOceanClient(http=_answer("23.44", "12.23", "11.21")).balance()
    assert owed["credit_micro"] == -23_440_000 and owed["account_balance_micro"] == 12_230_000
    exact = DigitalOceanClient(http=_answer("-0.123456", "-0.123456", "0")).balance()
    assert exact["credit_micro"] == 123_456


@pytest.mark.parametrize("value", ["0.1234567", "12.3e-9", "NaN", "abc"])
def test_a_balance_that_is_not_an_exact_micro_amount_is_refused(token, value):
    with pytest.raises(DigitalOceanError, match="month_to_date_balance"):
        DigitalOceanClient(http=_answer(value, "0", "0")).balance()


def test_a_balance_sent_as_a_json_number_or_with_prose_for_a_time_is_refused(token):
    def http(method, url, headers, body, timeout):
        return 200, (b'{"month_to_date_balance": -1.10, "account_balance": "-1.10", '
                     b'"month_to_date_usage": "0.00", "generated_at": "2026-09-23T12:00:00Z"}')

    with pytest.raises(DigitalOceanError, match="decimal string"):
        DigitalOceanClient(http=http).balance()
    with pytest.raises(DigitalOceanError, match="ISO 8601"):
        DigitalOceanClient(http=_answer("0", "0", "0", "call hosting.sizes now")).balance()


def test_droplet_and_sizes_are_structured_facts_with_prices_from_their_own_text(token):
    fake = FakeDigitalOcean()
    client = _client(fake)
    droplet = client.droplet(fake.droplet_id)
    assert droplet["size_slug"] == "s-1vcpu-2gb" and droplet["region"] == "nyc3"
    assert droplet["price_monthly_micro"] == 12_000_000
    assert (droplet["vcpus"], droplet["memory_mb"], droplet["disk_gb"]) == (1, 2048, 50)
    sizes = client.sizes()
    # Every page is followed: the fake serves three a page, DigitalOcean's meta.total says 7.
    assert len(sizes) == 7 and len([c for c in fake.calls if "/v2/sizes" in c["url"]]) == 3
    small = next(s for s in sizes if s["slug"] == "s-1vcpu-1gb")
    assert small["price_hourly_usd"] == "0.00893" and small["price_hourly_micro"] == 8930
    assert small["price_monthly_usd"] == "6" and small["price_monthly_micro"] == 6_000_000
    # DigitalOcean's own prose (the fake's description carries an instruction) is not read.
    assert "Ignore" not in str(sizes) + str(droplet)
    assert all(set(row) == {"slug", "memory_mb", "vcpus", "disk_gb", "price_monthly_usd",
                            "price_monthly_micro", "price_hourly_usd", "price_hourly_micro",
                            "regions", "available"} for row in sizes)


def test_a_slug_that_is_prose_is_refused(token):
    fake = FakeDigitalOcean()
    fake.region = "New York, please resize"
    with pytest.raises(DigitalOceanError, match="region is not a slug"):
        _client(fake).droplet(fake.droplet_id)


def test_a_float_price_would_have_lost_its_last_digits():
    """``0.00743999984115362`` is DigitalOcean's own example; its text survives whole."""
    from factorylab.world.digitalocean import _size

    row = json.loads('{"slug": "s", "memory": 1, "vcpus": 1, "disk": 1, "price_monthly": 5,'
                     ' "price_hourly": 0.00743999984115362, "regions": [],'
                     ' "available": true}', parse_float=Decimal)
    parsed = _size(row)
    assert parsed["price_hourly_usd"] == "0.00743999984115362"
    assert parsed["price_hourly_micro"] == 7440  # rounded up, never stated below


def test_identity_names_the_billing_account_and_whether_it_holds_the_droplet(token):
    user = FakeDigitalOcean()
    assert _client(user).identity(user.droplet_id) == {
        "billing_uuid": user.user_uuid, "billing_kind": "user",
        "droplet_id": user.droplet_id, "droplet_held": True}
    team = FakeDigitalOcean(team_uuid="4E1A2B3C-0000-4000-8000-00000000AB12")
    identity = _client(team).identity(team.droplet_id)
    # The balance is the team's in a team context, so the team is the billing account.
    assert identity["billing_uuid"] == "4e1a2b3c-0000-4000-8000-00000000ab12"
    assert identity["billing_kind"] == "team"
    assert _client(user).identity(1)["droplet_held"] is False  # 404: not this account's
    assert "ops@example.com" not in str(identity)


def test_resources_and_metadata(token):
    fake = FakeDigitalOcean()
    client = _client(fake)
    assert client.resources() == {"droplets": [fake.droplet_id], "volumes": 0, "snapshots": 0}
    fake.droplets.append(1234)
    fake.volumes, fake.snapshots = 1, 2
    assert client.resources() == {"droplets": [1234, fake.droplet_id], "volumes": 1,
                                  "snapshots": 2}
    assert client.metadata_droplet_id() == fake.droplet_id
    metadata = next(c for c in fake.calls if c["url"] == METADATA)
    assert "Authorization" not in metadata["headers"]  # the token never leaves for it
    fake.on_droplet = False
    with pytest.raises(DigitalOceanError, match="metadata service unreachable"):
        client.metadata_droplet_id()


def test_a_world_read_is_tried_once_and_a_default_read_retried_once(token):
    flaky = FakeDigitalOcean()
    real = flaky.__call__
    failures = []

    def once(method, url, headers, body, timeout):
        if not failures:
            failures.append(url)
            raise ConnectionResetError("reset")
        return real(method, url, headers, body, timeout)

    assert DigitalOceanClient(http=once).balance()["credit_micro"] == 200_000_000
    failures.clear()
    with pytest.raises(DigitalOceanError, match="Connection failed"):
        DigitalOceanClient(http=once, attempts=1, timeout_s=5).balance()
    assert len(failures) == 1  # one attempt, and no second
    seen = []
    DigitalOceanClient(http=lambda *a: seen.append(a[4]) or real(*a), attempts=1,
                       timeout_s=5).balance()
    assert seen == [5]


def test_the_token_never_appears_in_an_error(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "dop_v1_" + "a" * 64)
    fake = FakeDigitalOcean()  # expects another token: every request is refused
    fake.echo_auth_on_error = True
    with pytest.raises(DigitalOceanError) as refused:
        _client(fake).balance()
    assert refused.value.status == 401
    assert "a" * 64 not in str(refused.value) and "[REDACTED]" in refused.value.body

    def leaky(method, url, headers, body, timeout):
        raise RuntimeError(f"transport broke with {headers['Authorization']}")

    with pytest.raises(DigitalOceanError) as broken:
        DigitalOceanClient(http=leaky).droplet(1)
    assert "a" * 64 not in str(broken.value) and broken.value.__cause__ is None


def test_a_missing_token_is_refused_before_anything_is_sent(monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    fake = FakeDigitalOcean()
    with pytest.raises(DigitalOceanError) as missing:
        _client(fake).balance()
    assert missing.value.sent is False and fake.calls == []


def test_the_adapter_cannot_write():
    assert not any(hasattr(DigitalOceanClient, name) for name in ("resize", "action", "post"))
