"""Tests for fusion_gate. Run: uv run --with aiohttp --with pytest pytest plugins/fusion-gate"""

import asyncio
import dataclasses
import itertools

from aiohttp.test_utils import TestClient, TestServer

import fusion_gate as fg

FAST = fg.Config(wait_block=0.2, acquire_wait=0.5, idle_read=0.2, idle_write=0.3, idle_explicit=0.6,
                 ageing=60, place_kept=60, stale_call=0.1, auto_pause=False)
ids = (f"call{i}" for i in itertools.count())


def run(coro):
    return asyncio.run(coro)


def holder(gate):
    """The only lease, or None."""
    assert len(gate.leases) <= 1
    return next(iter(gate.leases.values()), None)


async def through(gate, session, kind="write", timeout=None):
    """Admit one call and finish it, as the hook's Pre and Post events would."""
    call = next(ids)
    why = await gate.admit(session, kind, FAST.wait_block if timeout is None else timeout, call=call)
    if why is None:
        gate.done(call)
    return why


def test_classify_separates_design_access():
    assert fg.classify("mcp__fusion360__fusion_mcp_read", {"queryType": "apiDocumentation"}) == "stateless"
    assert fg.classify("mcp__fusion360__fusion_mcp_read", {"queryType": "screenshot"}) == "read"
    assert fg.classify("mcp__fusion360__fusion_mcp_execute", {"featureType": "script", "object": {"readOnly": True}}) == "read"
    assert fg.classify("mcp__fusion360__fusion_mcp_execute", {"featureType": "script", "object": {}}) == "write"
    assert fg.classify("mcp__fusion360__fusion_mcp_execute", {"featureType": "document", "object": {"operation": "open"}}) == "write"
    assert fg.classify("mcp__fusion360__fusion_mcp_update", {"featureType": "undo"}) == "write"


def test_second_session_is_refused_while_first_holds():
    async def go():
        gate = fg.Gate(FAST)
        assert await through(gate, "a") is None
        assert await through(gate, "b") == "busy"
        assert holder(gate).session == "a"
    run(go())


def test_one_call_at_a_time_even_without_a_lease():
    async def go():
        gate = fg.Gate(FAST)
        assert await gate.admit("a", "write", 1, call="long") is None
        docs = asyncio.ensure_future(gate.admit("b", "stateless", 1, call="docs"))
        await asyncio.sleep(0.05)
        assert not docs.done()  # waits for a's call, not for a's lease
        gate.done("long")
        assert await docs is None
        assert holder(gate).session == "a"
    run(go())


def test_lease_ends_after_idle_and_passes_on():
    async def go():
        gate = fg.Gate(FAST)
        await through(gate, "a")
        await asyncio.sleep(FAST.idle_write + 0.05)
        gate.tick()
        assert holder(gate) is None
        assert await through(gate, "b") is None
    run(go())


def test_writer_keeps_the_lease_longer_than_a_reader():
    async def go():
        gate = fg.Gate(FAST)
        await through(gate, "reader", kind="read")
        assert holder(gate).idle == FAST.idle_read
        gate.release("reader", "test")
        await through(gate, "writer")
        assert holder(gate).idle == FAST.idle_write
    run(go())


def test_running_call_keeps_the_lease_past_idle():
    async def go():
        gate = fg.Gate(FAST)
        await gate.admit("a", "write", 1, call="rebuild")
        await asyncio.sleep(FAST.idle_write + 0.05)
        gate.tick()
        assert holder(gate).session == "a"
    run(go())


def test_reader_goes_before_earlier_writer():
    async def go():
        gate = fg.Gate(FAST)
        await through(gate, "a")
        writer = asyncio.ensure_future(gate.admit("w", "write", 2))
        await asyncio.sleep(0.01)
        reader = asyncio.ensure_future(gate.admit("r", "read", 2))
        await asyncio.sleep(0.01)
        gate.release("a", "test")
        assert await reader is None
        assert holder(gate).session == "r"
        gate.release("r", "test")
        assert await writer is None
    run(go())


def test_waiter_that_comes_back_keeps_its_place():
    async def go():
        gate = fg.Gate(FAST)
        await through(gate, "a")
        assert await gate.admit("b", "write", 0.05) == "busy"
        late = asyncio.ensure_future(gate.admit("c", "write", 2))
        await asyncio.sleep(0.01)
        back = asyncio.ensure_future(gate.admit("b", "write", 2))
        await asyncio.sleep(0.01)
        assert gate.queue() == ["b", "c"]
        gate.release("a", "test")
        assert await back is None
        gate.release("b", "test")
        assert await late is None
    run(go())


def test_pause_holds_even_the_holder_until_resume():
    async def go():
        gate = fg.Gate(FAST)
        await through(gate, "a")
        gate.set_pause(auto=False)
        assert await through(gate, "a", timeout=0.05) == "paused"
        waiting = asyncio.ensure_future(through(gate, "a", timeout=2))
        await asyncio.sleep(0.01)
        gate.resume("test")
        assert await waiting is None
        assert holder(gate).session == "a"
    run(go())


def test_fusion_down_clears_everything_and_refuses():
    async def go():
        gate = fg.Gate(FAST)
        await gate.admit("a", "write", 1, call="lost")
        gate.set_down(True)
        assert holder(gate) is None and not gate.calls
        assert await through(gate, "b", timeout=1) == "down"
        back = asyncio.ensure_future(gate.acquire("b", "rebuild", 5, ""))
        await asyncio.sleep(0.01)
        gate.set_down(False)
        await back
        assert holder(gate).session == "b"
    run(go())


def test_abandoned_call_is_cleared_once_fusion_is_idle():
    async def go():
        probed = asyncio.Event()

        async def probe():
            probed.set()

        gate = fg.Gate(FAST, probe=probe)
        await gate.admit("a", "write", 1, call="abandoned")  # the agent timed out; no Post event
        await asyncio.sleep(FAST.stale_call + 0.05)
        gate.tick()
        await asyncio.wait_for(probed.wait(), 1)
        await asyncio.sleep(0)
        assert not gate.calls
    run(go())


def test_first_call_of_a_lease_is_told_the_rules_once():
    async def go():
        gate = fg.Gate(FAST)
        gate.protected["assy_office"] = "Cal's unsaved changes"
        await gate.admit("a", "write", 1, call="first")
        assert "assy_office" in gate.done("first")
        await gate.admit("a", "write", 1, call="second")
        assert gate.done("second") == ""
    run(go())


def test_acquire_estimate_holds_the_lease_without_calls():
    async def go():
        gate = fg.Gate(FAST)
        await gate.acquire("a", "water box pass", FAST.idle_explicit * 2 / 60, "")
        await asyncio.sleep(FAST.idle_explicit + 0.05)
        gate.tick()
        assert holder(gate).session == "a"
    run(go())


def test_holder_is_not_told_to_keep_off_its_own_documents():
    async def go():
        gate = fg.Gate(FAST)
        gate.protect("assy_office", "Cal's unsaved changes", False)
        gate.protect("pelican", "water box work", False)
        await gate.acquire("a", "water box pass", None, "pelican")
        assert "assy_office" in gate._protected_line("a") and "pelican" not in gate._protected_line("a")
        assert "pelican" in gate._protected_line("b")
    run(go())


def test_protected_list_survives_a_restart(tmp_path):
    async def go():
        fg.Gate(FAST, state=tmp_path / "s.json").protect("assy_office", "unsaved", False)
        assert fg.Gate(FAST, state=tmp_path / "s.json").protected == {"assy_office": "unsaved"}
    run(go())


def test_waiter_between_acquire_runs_is_not_overtaken():
    async def go():
        gate = fg.Gate(FAST)
        await through(gate, "a")
        assert await gate.admit("b", "write", 0.05) == "busy"  # b's acquire run ended; it will run again
        late = asyncio.ensure_future(gate.admit("c", "write", 2))
        gate.release("a", "test")
        await asyncio.sleep(0.05)
        assert not late.done() and gate.queue() == ["b", "c"]
        assert await gate.admit("b", "write", 1) is None
        gate.release("b", "test")
        assert await late is None
    run(go())


def test_waiter_that_never_comes_back_stops_blocking():
    async def go():
        gate = fg.Gate(dataclasses.replace(FAST, rejoin=0.1))
        await through(gate, "a")
        assert await gate.admit("b", "write", 0.05) == "busy"
        late = asyncio.ensure_future(gate.admit("c", "write", 2))
        gate.release("a", "test")
        await asyncio.sleep(0.15)
        gate.tick()
        assert await late is None
    run(go())


async def declare(gate, session, documents):
    gate.intent[session] = {"reason": session, "minutes": None, "documents": documents}
    return await through(gate, session)


def test_agents_in_different_documents_hold_fusion_together():
    async def go():
        gate = fg.Gate(FAST)
        assert await declare(gate, "a", "water_box") is None
        assert await declare(gate, "b", "fan_shroud, assy_electrical") is None
        assert set(gate.leases) == {"a", "b"}
        assert await declare(gate, "c", "ASSY_ELECTRICAL") == "busy"  # names match without case
    run(go())


def test_sharing_off_makes_every_holder_exclusive():
    async def go():
        gate = fg.Gate(dataclasses.replace(FAST, share=False))
        assert await declare(gate, "a", "water_box") is None
        assert await declare(gate, "b", "fan_shroud") == "busy"
    run(go())


def test_calls_stay_one_at_a_time_between_sharing_holders():
    async def go():
        gate = fg.Gate(FAST)
        await declare(gate, "a", "water_box")
        await declare(gate, "b", "fan_shroud")
        assert await gate.admit("a", "write", 1, call="long") is None
        assert await gate.admit("b", "write", 0.05, call="next") == "busy"
        gate.done("long")
        assert await gate.admit("b", "write", 1, call="next") is None
    run(go())


def test_undeclared_session_works_around_declared_holders():
    async def go():
        gate = fg.Gate(FAST)
        await declare(gate, "a", "water_box")  # e.g. idle, waiting for Cal
        assert await through(gate, "plain") is None
        assert "water_box" in gate.welcome(gate.leases["plain"])
        assert await through(gate, "other_plain") == "busy"  # two undeclared could collide
    run(go())


def test_undeclared_holder_is_told_of_a_new_reservation():
    async def go():
        gate = fg.Gate(FAST)
        await through(gate, "plain")
        await declare(gate, "b", "fan_shroud")
        await gate.admit("plain", "write", 1, call="c")
        assert "fan_shroud" in gate.done("c")
    run(go())


def test_waiter_is_not_overtaken_on_a_document_it_wants():
    async def go():
        gate = fg.Gate(FAST)
        await declare(gate, "a", "x")
        gate.intent["b"] = {"documents": "x"}
        first = asyncio.ensure_future(gate.admit("b", "write", 2))
        await asyncio.sleep(0.01)
        assert await declare(gate, "c", "x, y") == "busy"  # b queued for x first
        assert await declare(gate, "d", "z") is None  # nobody wants z
        gate.release("a", "test")
        assert await first is None
    run(go())


def test_each_holder_gets_its_own_document_back():
    async def go():
        active = ["water_box v3"]
        switches = []

        async def activate(name):
            switches.append(name)
            replaced, active[0] = active[0], name
            return replaced, True

        gate = fg.Gate(FAST, activate=activate)
        await declare(gate, "a", "water_box")
        await declare(gate, "b", "fan_shroud")
        active[0] = "new_part"  # b's script opened a new document and left it active
        await through(gate, "a")
        await through(gate, "b")
        # b starts on its first declared document; each then gets back what it had active
        assert switches == ["fan_shroud", "water_box v3", "new_part"]
    run(go())


def test_holder_is_warned_when_its_document_cannot_be_restored():
    async def go():
        async def activate(name):
            return "water_box", name != "water_box"  # water_box has been closed

        gate = fg.Gate(FAST, activate=activate)
        await declare(gate, "a", "water_box")
        await declare(gate, "b", "fan_shroud")
        await gate.admit("a", "write", 1, call="c1")
        assert "couldn't make water_box active" in gate.done("c1")
        await through(gate, "b")
        assert gate.done("none") == ""  # the warning went to a only
    run(go())


def test_restart_keeps_holders_and_the_queue(tmp_path):
    async def go():
        state = tmp_path / "s.json"
        gate = fg.Gate(FAST, state=state)
        await gate.acquire("a", "water box pass", 45, "water_box")
        gate.intent["b"] = {"documents": "water_box"}
        waiting = asyncio.ensure_future(gate.admit("b", "write", 2))
        await asyncio.sleep(0.01)
        gate.tick()  # saves
        waiting.cancel()

        again = fg.Gate(FAST, state=state)
        assert holder(again).session == "a" and holder(again).minutes == 45
        again.intent["c"] = {"documents": "water_box"}
        assert await again.admit("c", "write", 0.05) == "busy"
        assert again.queue() == ["b", "c"]  # b keeps its place ahead of newcomers
    run(go())


def test_holder_waiting_for_its_call_is_not_in_the_queue(tmp_path):
    async def go():
        gate = fg.Gate(FAST, state=tmp_path / "s.json")
        await declare(gate, "a", "water_box")
        await declare(gate, "b", "fan_shroud")
        await gate.admit("a", "write", 1, call="long")
        b_call = asyncio.ensure_future(gate.admit("b", "write", 2, call="next"))
        await asyncio.sleep(0.01)
        assert gate.queue() == []
        gate.save()
        assert fg.Gate(FAST, state=tmp_path / "s.json").queue() == []
        gate.done("long")
        assert await b_call is None
    run(go())


def test_holder_stuck_behind_another_call_is_told_it_still_holds():
    async def go():
        gate = fg.Gate(dataclasses.replace(FAST, holder_wait=0.1))
        await declare(gate, "a", "water_box")
        await declare(gate, "b", "fan_shroud")
        await gate.admit("b", "write", 1, call="long")
        async with TestClient(TestServer(fg.make_app(gate))) as client:
            r = await client.post("/", json={"op": "admit", "session": "a", "kind": "write", "call": "x"})
            body = await r.json()
        assert body["ok"] is False and "Your lease is kept" in body["text"]
        assert "a" in gate.leases
    run(go())


def test_service_turns_refusal_into_a_denying_hook():
    async def go():
        gate = fg.Gate(FAST)
        await through(gate, "holder")
        async with TestClient(TestServer(fg.make_app(gate))) as client:
            r = await client.post("/", json={"op": "admit", "session": "other", "kind": "write", "call": "x"})
            body = await r.json()
        assert body["ok"] is False
        assert "NOT run" in body["text"] and "acquire" in body["text"]
    run(go())


def test_hook_output_shapes(monkeypatch):
    replies = {"admit": {"ok": False, "text": "busy"}, "done": {"ok": True, "text": "rules"}}
    monkeypatch.setattr(fg, "call", lambda payload, timeout: replies[payload["op"]])
    pre = fg.hook({"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "mcp__fusion360__fusion_mcp_execute",
                   "tool_input": {}, "tool_use_id": "t"})
    assert pre["hookSpecificOutput"]["permissionDecision"] == "deny"
    post = fg.hook({"hook_event_name": "PostToolUse", "session_id": "s", "tool_use_id": "t"})
    assert post["hookSpecificOutput"]["additionalContext"] == "rules"
    replies["admit"] = {"ok": True, "text": ""}
    assert fg.hook({"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "x", "tool_use_id": "t"}) is None
