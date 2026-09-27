"""``encode`` answers a subclass of a builtin by its own branch, never the builtin's.

The codec dispatches exact builtin types first; these values look like builtins
to ``isinstance`` and must still be encoded as what they are.
"""

from collections import OrderedDict, namedtuple
from enum import IntEnum, StrEnum

from factorylab.runtime.resume import decode, encode


class Level(IntEnum):
    LOW = 1


class Side(StrEnum):
    BUY = "buy"


class Named(str):
    definition = "a ratified definition"


Pair = namedtuple("Pair", "a b")


def test_subclasses_of_builtins_keep_their_own_encoding():
    assert encode(Level.LOW) == {"$enum": "Level", "value": 1}
    assert encode(Side.BUY) == {"$enum": "Side", "value": "buy"}
    assert encode(Named("norm")) == {"$norm": ["norm", "a ratified definition"]}
    assert encode(Pair(1, "x")) == {"$record": "Pair", "fields": {"a": 1, "b": "x"}}
    assert encode(OrderedDict(a=1)) == {"$map": [["a", 1]]}


def test_exact_builtins_encode_as_before():
    value = {"s": "text", 1: [True, None, 2, (3, "t")], "big": 1 << 13000, "f": 0.5}
    encoded = encode(value)
    assert encoded == {"$map": [
        ["s", "text"],
        [1, [True, None, 2, {"$tuple": [3, "t"]}]],
        ["big", {"$int": hex(1 << 13000)}],
        ["f", {"$float": "0.5"}]]}
    assert decode(encoded) == value
