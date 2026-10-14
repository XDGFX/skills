"""Tests for fusion_gate. Run: uv run --with aiohttp --with pytest pytest plugins/fusion-gate"""

import asyncio
import itertools

from aiohttp.test_utils import TestClient, TestServer

import fusion_gate as fg

FAST = fg.Config(wait_block=0.2, acquire_wait=0.5, idle_read=0.2, idle_write=0.3, idle_explicit=0.6,
                 ageing=60, place_kept=60, stale_call=0.1, auto_pause=False)
ids = (f"call{i}" for i in itertools.count())


def run(coro):
    return asyncio.run(coro)


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
        assert gate.lease.session == "a"
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
        assert gate.lease.session == "a"
    run(go())


def test_lease_ends_after_idle_and_passes_on():
    async def go():
        gate = fg.Gate(FAST)
        await through(gate, "a")
        await asyncio.sleep(FAST.idle_write + 0.05)
        gate.tick()
        assert gate.lease is None
        assert await through(gate, "b") is None
    run(go())


def test_writer_keeps_the_lease_longer_than_a_reader():
    async def go():
        gate = fg.Gate(FAST)
        await through(gate, "reader", kind="read")
        assert gate.lease.idle == FAST.idle_read
        gate.release("reader", "test")
        await through(gate, "writer")
        assert gate.lease.idle == FAST.idle_write
    run(go())


def test_running_call_keeps_the_lease_past_idle():
    async def go():
        gate = fg.Gate(FAST)
        await gate.admit("a", "write", 1, call="rebuild")
        await asyncio.sleep(FAST.idle_write + 0.05)
        gate.tick()
        assert gate.lease.session == "a"
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
        assert gate.lease.session == "r"
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
        assert gate.lease.session == "a"
    run(go())


def test_fusion_down_clears_everything_and_refuses():
    async def go():
        gate = fg.Gate(FAST)
        await gate.admit("a", "write", 1, call="lost")
        gate.set_down(True)
        assert gate.lease is None and not gate.calls
        assert await through(gate, "b", timeout=1) == "down"
        back = asyncio.ensure_future(gate.acquire("b", "rebuild", 5, ""))
        await asyncio.sleep(0.01)
        gate.set_down(False)
        await back
        assert gate.lease.session == "b"
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
        assert gate.lease.session == "a"
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
