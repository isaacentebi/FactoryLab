"""E3-C4: the seat sees itself, and the catalogue stops riding in front of the work.

Four claims are proved here.

The rendered block is fixed text for a fixed world: a golden file, so a change to
what a seat is told about itself is a change somebody chose rather than a change
that happened. Every number in it reconciles with the kernel — ``entitlements``,
``held_by``, the wallet's pots and its release schedule — so the block is a view
of the ledger and not a second set of books.

The shared directory exists: ``note.list`` and ``artifact.list`` page bounded
indexes with a privacy flag, so a reader need not already know a key or a hash.
The notebook's per-byte transfer toll is gone and byte-time rent is not.

And the prompt's shape is measured rather than asserted: every invocation records
its rendered bytes per section, and the stable prefix stays byte-identical across
two requests in one tick, which is the cache design the compaction had to survive.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from factorylab.cortex.request import SEAT_WORLD_KEYS, STABLE_WORLD_KEYS, Request
from factorylab.cortex.schematics import ACCOUNTING_FACTS, MIN_BURN_OBSERVATION_NS
from factorylab.kernel.money import money_to_usd
from factorylab.kernel.queue import PropensityRecord
from factorylab.kernel.wallet import ReleaseSchedule
from factorylab.runtime.notes import NotesSpec, charge_window
from tests.conftest import make_runtime
from tests.runtime.test_connectors import ledger_items

GOLDEN = Path(__file__).parent / "golden" / "e3_world_block.json"
#: A fixed instant, so the golden file is text about a world and not about a clock.
NOW_NS = 1_760_000_000 * 1_000_000_000
SEAT = "seed-decider"


def scripted_world():
    """One scripted world, pinned to a fixed clock and a fixed endowment schedule."""
    rt = make_runtime()
    rt.clock.now_ns = NOW_NS
    rt._manage_reserve_window()
    return rt


def decision(rt, owner: str = SEAT) -> str:
    """One open decision of this seat, with a deadline after this world's fixed clock."""
    handle = rt.queue.open(
        actor=owner, event_id="e3-c4", propensity=PropensityRecord(
            (owner,), (1.,), owner, 0, owner, "test"), channel="verdict",
        deadline_ns=NOW_NS + 86_400 * 10**9, parent_handle=None, cost_ceiling=10_000_000)
    rt.handle_to_assembly[handle] = owner
    return handle


def request_for(rt, seat: str = SEAT, handle: str = "decision-golden") -> Request:
    """The request a seat is actually given, with its identity stamped as the assembly does."""
    req = rt._request(handle, "Respond to event Tick on scripted.",
                      {"kind": "Tick", "payload": {"index": 1}, "world": rt._world_block()},
                      {"type": "object", "properties": {"action": {"type": "string"}}},
                      NOW_NS + 60 * 1_000_000_000, "verdict")
    return replace(req, inputs={**req.inputs, "you": seat})


# ------------------------------------------------------------------ the golden block


def test_the_rendered_block_is_the_golden_file_for_a_scripted_world():
    """What a seat is told about itself, fixed. Regenerate deliberately, never by hand.

    Regenerated for R3-E: the block's slots are now GPT-6's third-reading §8
    ``YOU`` template — seat, lineage, clock with the tick duration, working
    state, spending authority, provider inventory, venue accounts and pending
    conversions by custody, runway, subscription with its next eligible tick,
    open commitments, outcomes with exact ids oldest-first, and the directory —
    and the market ages and the coalesced fold have left it for ``WORLD UPDATE``,
    which is where facts about the world belong.
    """
    block = request_for(scripted_world()).seat_block()
    rendered = json.dumps(block, sort_keys=True, indent=2) + "\n"
    if not GOLDEN.exists():  # pragma: no cover - first generation only
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(rendered)
    assert rendered == GOLDEN.read_text()
    assert set(block) == {
        "seat", "lineage", "request", "clock", "working_state", "spending_authority",
        "provider_inventory", "venue_accounts", "pending_conversions", "runway",
        "subscription", "open_commitments", "outcomes", "directory"}
    assert set(block["outcomes"]) == {"unread_count", "items", "more"}
    assert set(block["clock"]) == {
        "now_utc", "tick_index", "tick_interval_seconds", "last_successful_delivery"}


def test_the_accounting_facts_are_the_architects_own_sentences_and_reach_every_call():
    """They say what the numbers mean, and they hold for every call in this world.

    R3-E moves where they ride, not whether they arrive. The stable prefix is now
    the WORLD CONTRACT and the base capability index and nothing else (GPT-6's
    third reading, §8: byte stability "does not require copying every
    institutional description into that prefix"), so these sentences are rendered
    with the rest of the institutional disclosure, in ``INPUTS``. Every request
    still carries all seven, once.
    """
    rt = scripted_world()
    block = rt._world_block()
    assert block["accounting_facts"] == list(ACCOUNTING_FACTS)
    assert "accounting_facts" in STABLE_WORLD_KEYS
    req = request_for(rt)
    text, prefix = req.prompt_text(), req.stable_prefix()
    for sentence in ACCOUNTING_FACTS:
        assert text.count(json.dumps(sentence)[1:-1]) == 1
        assert sentence not in prefix
    assert "A paid thought consumes the named budget" in text


def test_the_norm_definitions_reach_the_block_and_the_wake():
    """C3 put the clauses in the charter object; both readings must carry them.

    The block carries them inside the charter text it already renders, so they
    ride in the cached prefix once rather than twice. The wake serialises norms
    through ``Norm``, which is a ``str``, so without an explicit field the
    definitions would be dropped exactly where a reader has no other source.
    """
    from factorylab.charter.charter import EDITION3_NORMS
    from factorylab.runtime.wake import public_window_item

    rt = scripted_world()
    rt.charter = replace(rt.charter, norms=EDITION3_NORMS, cards=())
    block = rt._world_block()
    prefix = request_for(rt).stable_prefix()
    for norm in EDITION3_NORMS:
        assert norm.definition in block["charter"]
        assert json.dumps(norm.definition)[1:-1] in prefix
    assert "Measurements are defeasible evidence of the values" in block["charter"]
    wake = public_window_item(rt, window=rt.window.index, event=rt.n)
    assert wake["charter"]["norm_definitions"] == {
        str(n): n.definition for n in EDITION3_NORMS}
    assert wake["charter"]["norms"] == [str(n) for n in EDITION3_NORMS]


def test_the_root_wallet_only_impression_and_the_mechanism_claims_are_gone():
    """A seat is no longer told it has a pot it has a much smaller entitlement over."""
    rt = scripted_world()
    block = rt._world_block()
    assert "wallet_balance_usd" not in block
    supply = json.dumps(block["compute_supply"])
    for claim in ("cannot drift", "Machinery you have learned belongs", "useful"):
        assert claim not in supply
    # What it says instead is capability and cost.
    assert "credit" in supply and "provider_inventory" in supply


def test_a_seat_reads_its_own_lineage_and_never_another_seats_account():
    """Self-context, not a directory of everyone's money."""
    rt = scripted_world()
    other = next(s for s in rt.budget.seats() if s != SEAT)
    rt.budget.grant(other, 5_000_000, "fixture")
    text = request_for(rt).prompt_text()
    you = text.split("YOU\n")[1].split("\n\nWORLD UPDATE")[0]
    assert f'"lineage": "{rt.budget.lineage(SEAT)}"' in you
    assert other not in you
    # The world block holds every seat; the rendering shows exactly one account.
    # The rows are a list, not a map: an id that keys a fact is an edge (A8).
    rows = rt._world_block()["seats"]
    assert {row["seat_id"] for row in rows} == set(rt.budget.seats())
    assert not any(f'"{row["seat_id"]}": ' in json.dumps(rows) for row in rows)
    assert "seats" in SEAT_WORLD_KEYS
    assert text.count('"available_micro_usd"') == 1
    assert text.count('"lineage"') == 1


# ------------------------------------------------------------ reconciled with the ledger


def test_every_resource_number_reconciles_with_the_kernel():
    rt = scripted_world()
    handle = decision(rt)
    reservation = rt.wallet.reserve(2_000, handle, "model:fake-haiku")
    assert reservation is not None
    authority = request_for(rt).seat_block()["spending_authority"]
    entitlements = rt.budget.entitlements()
    assert authority["available_micro_usd"] == entitlements[SEAT]
    assert entitlements[SEAT] == rt.budget.entitlement(SEAT)
    assert authority["held_micro_usd"] == rt.budget.held_by(SEAT)
    # Net of holds, by the book's own definition: the two agree, to the micro.
    assert authority["entitlement_micro_usd"] == (
        rt.budget.entitlement(SEAT) + rt.budget.held_by(SEAT))
    inventory = request_for(rt).seat_block()["provider_inventory"]
    pots = rt.wallet.pots()
    assert inventory["openrouter_usd"] == str(money_to_usd(pots["seed"]))
    assert inventory["venice_usd"] == str(money_to_usd(pots["sellers"]["venice"]))
    # The factory's own money is still published, once, with the rest of the
    # institutional disclosure; what ``YOU`` carries is this seat's authority.
    world = rt._world_block()["world_resources"]
    assert world["root_unlocked_usd"] == str(money_to_usd(rt.wallet.unlocked))
    assert world["root_locked_usd"] == str(money_to_usd(rt.wallet.locked))


def test_the_next_release_and_its_share_follow_the_wallet_schedule_and_base_share():
    """The share a seat is promised is the one ``BudgetBook`` will actually grant it."""
    rt = make_runtime()
    rt.clock.now_ns = NOW_NS
    rt._manage_reserve_window()
    tranche = 60_000_000
    rt.wallet._Wallet__release_schedule = ReleaseSchedule(((7 * 86_400 * 10**9, tranche),))
    # Escrowed backing: booked in the balance, spendable by nobody until it releases.
    rt.wallet._Wallet__locked = tranche
    rt.wallet._Wallet__balance += tranche
    rt.wallet.launch(NOW_NS)
    block = request_for(rt).seat_block()["spending_authority"]["next_release"]
    assert block["root_amount_usd"] == str(money_to_usd(tranche))
    assert block["at_utc"] == "2025-10-16T08:53:20Z"  # seven days after the fixed launch
    lineages = rt.budget.lineages()
    numerator, denominator = rt.budget.base_share.as_integer_ratio()
    expected = tranche * numerator // denominator // len(lineages)
    assert SEAT in {row["head"] for row in lineages.values()}
    assert block["your_share_usd"] == str(money_to_usd(expected))
    # And that is what a real release grants, to the micro.
    before = rt.budget.entitlement(SEAT)
    rt.budget.on_release(rt.wallet.release_due(NOW_NS + 8 * 86_400 * 10**9))
    assert rt.budget.entitlement(SEAT) - before == expected


def test_a_runway_is_never_asserted_from_too_little_history():
    """A rate measured over two calls is not evidence about a week."""
    rt = scripted_world()
    rt._record_spend(SEAT, 1_000)
    block = request_for(rt).seat_block()
    assert block["runway"]["days_low"] == "insufficient history"
    assert block["spending_authority"]["next_release"][
        "reachable_at_observed_burn"] == "unknown"

    rt.clock.now_ns += MIN_BURN_OBSERVATION_NS
    rt._record_spend(SEAT, 1_000)
    runway = request_for(rt).seat_block()["runway"]
    assert runway["days_low"] != "insufficient history"
    assert float(runway["days_low"]) <= float(runway["days_high"])
    assert runway["observed_over"] == "6h"


def test_open_commitments_are_this_seats_own_outstanding_decisions():
    rt = scripted_world()
    handle = decision(rt)
    commitments = request_for(rt).seat_block()["open_commitments"]
    handles = [row["handle"] for row in commitments["open_decisions"]]
    assert handle in handles
    assert commitments["open_decision_count"] == len(rt.queue.outstanding(SEAT))
    assert all(rt.queue.get(h).actor == SEAT for h in handles)


def test_market_data_carries_its_age_and_says_when_it_is_missing():
    """R3-E: the ages are the WORLD UPDATE's observation window, not the seat's own state.

    How old a price is is a fact about the world, identical for every seat woken
    in this tick, so it is rendered with the rest of the world's moving facts and
    a source that could not be read is named in ``unavailable_observations``
    rather than left to be inferred from a null.
    """
    def freshness(rt):
        return request_for(rt).world_update_block()["observation_window"]["source_freshness"]

    rt = scripted_world()
    as_of = freshness(rt)
    assert as_of["BTC"] == {"as_of_utc": None, "age": None, "missing": True, "stale": True}
    reasons = request_for(rt).world_update_block()["unavailable_observations"]
    assert {"source": "mid:BTC", "reason": "no print observed"} in reasons
    rt.recent_mids["BTC"] = [{"t_s": NOW_NS // 10**9, "mid": "100"}]
    fresh = freshness(rt)["BTC"]
    assert fresh["missing"] is False and fresh["stale"] is False
    rt.clock.now_ns += 10 * rt.tick_clock.interval_ns
    stale = freshness(rt)["BTC"]
    assert stale["missing"] is False and stale["stale"] is True
    assert any(row["source"] == "mid:BTC" and "old" in row["reason"]
               for row in request_for(rt).world_update_block()["unavailable_observations"])


def test_the_coalesced_update_is_continuity_and_is_rendered_exactly_once():
    """C2's fold is everything that happened while this seat was not awake."""
    rt = scripted_world()
    fold = {"from_tick": 3, "to_tick": 9, "prints": 4,
            "coins": {"BTC": {"first": "100", "last": "104", "high": "105", "low": "99"}}}
    req = request_for(rt)
    payload = {**req.inputs["payload"], "since_you_last_woke": fold}
    req = replace(req, inputs={**req.inputs, "payload": payload})
    update = req.world_update_block()
    assert update["changes_since_last_successful_delivery"] == fold
    text = req.prompt_text()
    assert text.count('"since_you_last_woke"') == 0
    assert text.count('"changes_since_last_successful_delivery"') == 1
    block, work = text.split("\n\nREQUEST\n", 1)
    assert '"from_tick": 3' in block and '"from_tick": 3' not in work
    # Nothing else about the event moved.
    assert '"index": 1' in work
    # A request with no fold says so; it never renders an empty one as if the
    # world had stood still while this seat slept.
    absent = request_for(rt).world_update_block()
    assert absent["changes_since_last_successful_delivery"]["status"] == "unavailable"


def test_the_provider_inventory_shows_the_committed_block_beside_the_last_read():
    rt = scripted_world()
    inventory = request_for(rt).seat_block()["provider_inventory"]
    committed = inventory["committed_at_launch"]
    assert committed["openrouter_usd"] == str(money_to_usd(rt.m.providers.openrouter_micro))
    assert committed["venice_usd"] == str(money_to_usd(rt.m.providers.venice_micro))
    assert "cannot pay for a Venice model" in inventory["as_of"]
    # The observed side is the cached treasury read, and never a fresh rail call.
    assert inventory["openrouter_usd"] == str(money_to_usd(rt.wallet.pots()["seed"]))


def test_continuity_renders_the_request_inputs_c1_supplies_and_absence_when_it_does_not():
    rt = scripted_world()
    absent = request_for(rt).seat_block()
    # A source C1 did not supply is unavailable, not zero: a request built without
    # an inbox read has not established that this seat has nothing waiting.
    assert absent["working_state"] == "unavailable"
    assert absent["outcomes"] == {
        "unread_count": "unavailable", "items": [], "more": "unavailable"}
    empty = replace(request_for(rt), inputs={
        **request_for(rt).inputs, "unread_outcomes": {"count": 0, "items": []}}).seat_block()
    assert empty["outcomes"] == {"unread_count": 0, "items": [], "more": 0}
    req = request_for(rt)
    state = {"sha": "a" * 64, "bytes": 12}
    outcomes = {"count": 3, "items": [{"handle": "decision-1", "outcome_id": "outcome:7"}]}
    carried = replace(req, inputs={**req.inputs, "your_state": state,
                                   "unread_outcomes": outcomes}).seat_block()
    assert carried["working_state"] == state
    # Oldest first, the exact id the inbox stamped, and what is not inline said as
    # a count rather than dropped.
    assert carried["outcomes"] == {
        "unread_count": 3, "more": 2,
        "items": [{"handle": "decision-1", "outcome_id": "outcome:7"}]}
    # Without an inbox id the decision handle is the address, because that is what
    # ``outcome.get`` and ``ack_through`` already accept. Nothing is minted here.
    no_id = replace(req, inputs={**req.inputs, "unread_outcomes": {
        "count": 1, "items": [{"handle": "decision-2"}]}}).seat_block()
    assert no_id["outcomes"]["items"] == [
        {"handle": "decision-2", "outcome_id": "decision-2"}]


# --------------------------------------------------------------- the shared directory


def notes_runtime(keys: int):
    rt = scripted_world()
    handle = decision(rt)
    for index in range(keys):
        result, _cost = rt._run_tool(SEAT, handle, {
            "tool": "note.put", "args": {"key": f"k{index:03d}", "text": f"fact {index}"}})
        assert "error" not in result
    return rt, handle


def test_note_list_pages_a_bounded_index_with_a_cursor_and_no_text():
    rt, handle = notes_runtime(60)
    first, cost = rt._run_tool(SEAT, handle, {"tool": "note.list", "args": {}})
    assert cost == 0 and first["count"] == 60 and len(first["items"]) == 50
    assert first["next_cursor"] is not None
    row = first["items"][0]
    assert set(row) == {"key", "title", "type", "bytes", "version", "owner",
                        "updated_window", "public"}
    assert row["owner"] == SEAT and row["public"] is True and row["type"] == "note"
    assert "fact" not in json.dumps(first)  # an index, never the contents
    second, _ = rt._run_tool(SEAT, handle, {
        "tool": "note.list", "args": {"cursor": first["next_cursor"]}})
    assert len(second["items"]) == 10 and second["next_cursor"] is None
    keys = [r["key"] for r in first["items"] + second["items"]]
    assert len(set(keys)) == 60
    # An unknown cursor ends the listing; paging can never loop.
    lost, _ = rt._run_tool(SEAT, handle, {"tool": "note.list", "args": {"cursor": "nope"}})
    assert lost["items"] == [] and lost["next_cursor"] is None
    assert ledger_items(rt, "note.list")[-1]["count"] == 60


def test_artifact_list_pages_by_owner_and_publishes_the_privacy_flag():
    rt = scripted_world()
    handle = decision(rt)
    mine = [rt.artifacts.put(f"body {i}".encode(), owner=SEAT, kind="state", public=True)
            for i in range(55)]
    theirs = rt.artifacts.put(b"someone else", owner="eval-a", kind="state")
    page, cost = rt._run_tool(SEAT, handle, {"tool": "artifact.list", "args": {}})
    assert cost == 0 and page["count"] == 56 and len(page["items"]) == 50
    rest, _ = rt._run_tool(SEAT, handle, {
        "tool": "artifact.list", "args": {"cursor": page["next_cursor"]}})
    assert len(rest["items"]) == 6 and rest["next_cursor"] is None
    shas = {row["sha"] for row in page["items"] + rest["items"]}
    assert shas == set(mine) | {theirs}
    owned, _ = rt._run_tool(SEAT, handle, {"tool": "artifact.list", "args": {"owner": "eval-a"}})
    assert [row["sha"] for row in owned["items"]] == [theirs]
    assert owned["items"][0]["owner"] == "eval-a"
    # The published flag is the store's own (C1), never a guess: a seat's private
    # artifact is listed, so it can be found, and is marked as what it is.
    assert all(row["public"] is True for row in page["items"] if row["sha"] in set(mine))
    assert owned["items"][0]["public"] is False
    assert owned["items"][0]["type"] == "state"
    assert not rt.artifacts.visible_to(theirs, SEAT)
    assert rt.artifacts.visible_to(mine[0], "eval-a")


def test_the_world_block_previews_the_directory_and_names_where_the_rest_is():
    rt, _handle = notes_runtime(12)
    directory = request_for(rt).seat_block()["directory"]
    assert directory["notes"]["count"] == 12 and len(directory["notes"]["keys"]) == 10
    assert "note.list" in directory["paging"] and "artifact.list" in directory["paging"]
    assert {"note.list", "artifact.list"} <= set(rt.tool_specs)
    # §8's ``directory`` slot is this seat's own index: the keys it owns and the
    # artifacts it may read. The shared directory's newest rows are still
    # published, once, with the rest of the institutional disclosure.
    shared = rt._world_block()["continuity"]["shared_directory_changes"]
    assert shared["notes"]["count"] == 12


# ------------------------------------------------------- the toll goes, the rent stays


def test_the_transfer_toll_is_gone_and_byte_time_rent_remains():
    rt = scripted_world()
    rt.m = replace(rt.m, notes=NotesSpec(micro_per_byte_day="720"))
    handle = decision(rt)
    flat = rt.m.notes.byte_window_micro
    long_text = "x" * 4_000
    result, cost = rt._run_tool(SEAT, handle, {
        "tool": "note.put", "args": {"key": "big", "text": long_text}})
    assert "error" not in result
    # 4,003 bytes would have cost 4,003 micro-USD to move. It costs the flat price.
    assert cost == flat and rt.notes["big"]["bytes"] == 4_003
    read, read_cost = rt._run_tool("eval-a", handle, {
        "tool": "note.get", "args": {"key": "big"}})
    assert read["text"] == long_text and read_cost == flat

    # Rent is untouched: one two-minute window at this rate is one micro per byte.
    before = rt.wallet.balance
    rt.clock.now_ns += rt.m.novelty.window_ns
    charge_window(rt)
    assert rt.wallet.balance == before - 4_003
    assert ledger_items(rt, "note.rent")[-1]["cost"] == 4_003


# ----------------------------------------------------------- measured, and still cached


def test_rendered_bytes_per_section_are_recorded_on_every_invocation():
    rt = scripted_world()
    handle = decision(rt)
    rt.consequences.start(handle, rt.n)
    rt._invoke(SEAT, request_for(rt, handle=handle), "producer")
    sections = ledger_items(rt, "invocation")[-1]["sections"]
    assert set(sections) >= {"stable_prefix", "you", "request", "inputs", "total"}
    assert sections["total"] == sum(v for k, v in sections.items() if k != "total")
    # R3-E: the prefix is the WORLD CONTRACT and the base capability index, and is
    # no longer the bulk of the prompt. What it is instead is small, whole, and
    # byte-identical — the institutional disclosure it used to carry is rendered
    # once, with the work, where it can be read at the moment it matters.
    assert sections["you"] > 0 and 0 < sections["stable_prefix"] < sections["inputs"]
    assert sections["world_update"] > 0 and sections["outcome_contract"] > 0
    # The counts are the bytes actually sent, not a model of them.
    req = request_for(rt, handle=handle)
    assert req.section_bytes()["total"] == len(req.prompt_text().encode("utf-8"))
    # And the observatory folds them into the page, as rows: a section name such
    # as "propensity" is a sealed key when it names a field, never when it names
    # a count of bytes.
    from factorylab.runtime.wake import _Observatory

    observatory = _Observatory()
    for item in ledger_items(rt, "invocation"):
        observatory.feed({**item, "kind": "invocation"})
    view = observatory._prompt_sections()
    assert view["prompts"] == 1
    rows = {row["section"]: row for row in view["sections"]}
    assert rows["stable_prefix"]["mean_bytes"] == sections["stable_prefix"]
    assert rows["you"]["total_bytes"] == sections["you"]


def test_the_catalogue_is_an_index_in_the_prefix_and_the_schemas_are_fetched():
    rt = scripted_world()
    block = rt._world_block()
    listed = next(s for s in block["tools"] if s["id"] == "venue.instruments")
    assert set(listed) == {"id", "kind", "description", "price_micro_per_call", "args"}
    assert "args_schema" not in json.dumps(block["tools"])
    assert isinstance(block["proposal_shapes"]["amendment"], str)
    # Every registered tool is still named, so nothing became undiscoverable.
    assert {s["id"] for s in block["tools"]} == set(rt.tool_specs)
    assert set(block["proposal_shapes"]) == set(rt.PROPOSAL_SHAPES)
    handle = decision(rt)
    found, _cost = rt._run_tool(SEAT, handle, {
        "tool": "catalogue.search", "args": {"substring": "venue.place_market"}})
    spec = next(s for s in found["tools"] if s["id"] == "venue.place_market")
    assert spec["args_schema"] == rt.tool_specs["venue.place_market"]["args_schema"]
    shapes, _ = rt._run_tool(SEAT, handle, {
        "tool": "catalogue.search", "args": {"substring": "amendment"}})
    assert shapes["proposal_shapes"]["amendment"] == rt.PROPOSAL_SHAPES["amendment"]


def test_the_prefix_is_byte_identical_across_two_requests_in_one_tick():
    """The cache design the compaction had to survive."""
    rt = scripted_world()
    first = request_for(rt, handle="decision-a")
    second = request_for(rt, seat="eval-a", handle="decision-b")
    assert first.stable_prefix() == second.stable_prefix()
    assert first.prompt_text().startswith(first.stable_prefix())
    # And the YOU block, which does move, is entirely after it.
    assert first.seat_text() != second.seat_text()
    assert first.prompt_text()[len(first.stable_prefix()):].startswith("YOU\n")


@pytest.mark.parametrize("key", sorted(SEAT_WORLD_KEYS))
def test_no_world_key_is_rendered_twice_or_dropped(key):
    """R3-E adds two more places a world key can be rendered; the partition is exact.

    A key of the world block is rendered in the stable prefix, in ``YOU``, in
    ``WORLD UPDATE``, or inside ``INPUTS`` — in exactly one of them. The WORLD
    UPDATE sources stay in the block, because the block is the runtime's own
    disclosure surface and more than the prompt reads it, but they are rendered
    only through ``world_update``.
    """
    from factorylab.cortex.request import (
        PREFIX_SOURCE_KEYS,
        PREFIX_WORLD_KEY,
        UPDATE_SOURCE_KEYS,
        UPDATE_WORLD_KEY,
    )

    rt = scripted_world()
    req = request_for(rt)
    block = req.inputs["world"]
    stable, moving = req._world_split()
    assert key in block and key not in stable and key not in moving
    elsewhere = (set(SEAT_WORLD_KEYS) | {PREFIX_WORLD_KEY, UPDATE_WORLD_KEY}
                 | UPDATE_SOURCE_KEYS | PREFIX_SOURCE_KEYS)
    assert set(stable) | set(moving) | elsewhere == set(block)
    assert not set(stable) & set(moving)
    assert not (set(stable) | set(moving)) & elsewhere
