"""A fake adapter cannot bypass the mainnet world-name restriction."""

import pytest

from factorylab.runtime.worlds import manifest_from_dict
from tests.runtime.test_manifests import _base


def test_fake_mainnet_outside_funded_is_refused():
    data = _base()
    data["exchange"] = {"kind": "fake", "mainnet": True}
    with pytest.raises(ValueError, match="mainnet is only allowed"):
        manifest_from_dict(data)
