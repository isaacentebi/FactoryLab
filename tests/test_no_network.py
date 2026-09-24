"""The autouse guard in conftest.py: no test in the check or gate tiers can reach the
network, and code that swallows the refusal cannot hide the attempt."""

import socket
import urllib.request

import pytest

from tests.conftest import NetworkForbidden


def test_every_outbound_path_is_refused_naming_where_it_went(_no_network):
    from factorylab.world import polymarket, x402

    reaches = [
        (lambda: x402.http_request("GET", "https://mainnet.base.org", None, {}),
         "https://mainnet.base.org"),
        (lambda: polymarket.http_get_json("https://gamma-api.polymarket.com/markets"),
         "https://gamma-api.polymarket.com/markets"),
        (lambda: urllib.request.urlopen("https://example.com/"), "https://example.com/"),
        (lambda: socket.getaddrinfo("example.com", 443), "example.com"),
        (lambda: socket.create_connection(("93.184.216.34", 443)), "93.184.216.34"),
        (lambda: socket.socket().connect(("93.184.216.34", 443)), "93.184.216.34"),
    ]
    for reach, named in reaches:
        with pytest.raises(NetworkForbidden, match="tests may not touch the network") as refused:
            reach()
        assert named in str(refused.value)
    assert len(_no_network) == len(reaches)
    _no_network.clear()


def test_an_attempt_swallowed_by_the_code_is_still_remembered(_no_network):
    from factorylab.world.evm import BASE, EVM, Pending

    with pytest.raises(Pending):  # the EVM turns any transport failure into a retry
        EVM(BASE, None).block()
    assert _no_network and "mainnet.base.org" in _no_network[0]
    _no_network.clear()


def test_loopback_and_unix_sockets_stay_open():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        socket.create_connection(server.getsockname(), timeout=2).close()
        client = socket.socket()
        client.connect(("localhost", server.getsockname()[1]))
        client.close()
    finally:
        server.close()
    left, right = socket.socketpair()
    left.sendall(b"x")
    assert right.recv(1) == b"x"
    left.close()
    right.close()
