"""The DigitalOcean adapter reads exactly, redacts its token and never resends a write.

Every test runs against ``tests.digitalocean_fake`` at the HTTP boundary; nothing
here reaches the network.
"""

import json

import pytest

from factorylab.world.digitalocean import TOKEN_ENV, DigitalOceanClient, DigitalOceanError
from tests.digitalocean_fake import TOKEN, FakeDigitalOcean


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    return TOKEN


def _client(fake):
    return DigitalOceanClient(http=fake)


def _answer(balance, account, usage):
    def http(method, url, headers, body, timeout):
        return 200, json.dumps({"month_to_date_balance": balance, "account_balance": account,
                                "month_to_date_usage": usage,
                                "generated_at": "2026-09-23T12:00:00Z"}).encode()
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


def test_a_balance_sent_as_a_json_number_is_refused_not_read_through_a_float(token):
    def http(method, url, headers, body, timeout):
        return 200, (b'{"month_to_date_balance": -1.10, "account_balance": "-1.10", '
                     b'"month_to_date_usage": "0.00", "generated_at": "2026-09-23T12:00:00Z"}')

    with pytest.raises(DigitalOceanError, match="decimal string"):
        DigitalOceanClient(http=http).balance()


def test_droplet_and_sizes_parse_prices_from_their_own_text(token):
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


def test_a_float_price_would_have_lost_its_last_digits():
    """``0.00743999984115362`` is DigitalOcean's own example; its text survives whole."""
    from factorylab.world.digitalocean import _size

    row = json.loads('{"slug": "s", "memory": 1, "vcpus": 1, "disk": 1, "price_monthly": 5,'
                     ' "price_hourly": 0.00743999984115362, "regions": [],'
                     ' "available": true}', parse_float=__import__("decimal").Decimal)
    parsed = _size(row)
    assert parsed["price_hourly_usd"] == "0.00743999984115362"
    assert parsed["price_hourly_micro"] == 7440  # rounded up: the only use is a cap


def test_the_resize_body_and_answer_are_what_digitalocean_documents(token):
    fake = FakeDigitalOcean()
    action = _client(fake).resize(fake.droplet_id, "s-2vcpu-4gb", False)
    assert fake.posts == [{"type": "resize", "size": "s-2vcpu-4gb", "disk": False}]
    assert action["status"] == "in-progress" and action["type"] == "resize"
    assert _client(fake).action(action["id"])["status"] == "in-progress"
    fake.finish(action["id"])
    assert _client(fake).action(action["id"])["status"] == "completed"
    with pytest.raises(DigitalOceanError, match="boolean disk"):
        _client(fake).resize(fake.droplet_id, "s-2vcpu-4gb", "false")


def test_a_write_is_never_retried_and_a_read_is_retried_once(token):
    fake = FakeDigitalOcean()
    fake.lose_post_answer = True
    with pytest.raises(DigitalOceanError) as lost:
        _client(fake).resize(fake.droplet_id, "s-2vcpu-4gb", False)
    assert lost.value.sent is True and len(fake.posts) == 1
    flaky = FakeDigitalOcean()
    real = flaky.__call__
    failures = []

    def once(method, url, headers, body, timeout):
        if not failures:
            failures.append(url)
            raise ConnectionResetError("reset")
        return real(method, url, headers, body, timeout)

    assert DigitalOceanClient(http=once).balance()["credit_micro"] == 200_000_000
    assert len(failures) == 1


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
        _client(fake).resize(fake.droplet_id, "s-2vcpu-4gb", False)
    assert missing.value.sent is False and fake.calls == []
