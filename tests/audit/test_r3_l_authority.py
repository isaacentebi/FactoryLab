"""Every writing ancestor must still hold an open account."""

from dataclasses import replace

from tests.audit.test_r3_b_authority import _producing_decision
from tests.conftest import make_runtime


def test_closed_ancestor_refuses_child_write(monkeypatch):
    rt = make_runtime()
    parent = _producing_decision(rt)
    child = _producing_decision(rt)
    get = rt.queue.get
    monkeypatch.setattr(rt.queue, "get", lambda h: replace(get(h), parent_handle=parent)
                        if h == child else get(h))
    assert rt._may_write(child)
    rt.consequences.finish(parent, 0)
    rt.consequences.resolve(rt.n)
    assert not rt.consequences.account_open(parent)
    assert rt.consequences.account_open(child)
    assert not rt._may_write(child)
