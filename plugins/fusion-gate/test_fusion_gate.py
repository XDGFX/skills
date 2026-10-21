"""Tests for fusion_gate. Run: uv run --with aiohttp --with pytest pytest plugins/fusion-gate"""

import asyncio
import dataclasses
import itertools
import json
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest
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


# --- a stand-in for Fusion: the gate's real GUARD_SCRIPT runs against these documents ---------

class FakeDesign:
    def __init__(self):
        self.timeline = types.SimpleNamespace(count=10, markerPosition=10)
        self.userParameters = types.SimpleNamespace(count=5)
        self.rootComponent = types.SimpleNamespace(occurrences=types.SimpleNamespace(count=2))


class FakeDoc:
    def __init__(self, fusion, name, stuck=False, reference=False):
        self.fusion, self.name, self.stuck, self.reference = fusion, name, stuck, reference
        self.isModified = False
        self.design = FakeDesign()
        self.products = types.SimpleNamespace(itemByProductType=lambda _type: self.design)

    def edit(self):
        """What a script that adds a feature does to the document."""
        self.design.timeline.count += 1
        self.isModified = True

    @property
    def documentReferences(self):
        if self.reference:
            raise RuntimeError("open only as a reference")
        return types.SimpleNamespace(count=0)

    def activate(self):
        if not self.stuck:
            self.fusion.active = self
        return not self.stuck


class FakeDocuments(list):
    def __init__(self, fusion):
        super().__init__(fusion.docs)
        self.fusion = fusion

    def add(self, _type):
        return self.fusion.open("Untitled")


class FakeFusion:
    """What adsk.core.Application.get() returns while the gate's check runs."""

    def __init__(self, *names, active=None):
        self.docs = [FakeDoc(self, n) for n in names]
        self.active = self.doc(active) if active else None
        self.checks = []
        self.slow = 0.0
        self.broken = False
        self.result = Path(tempfile.mkdtemp()) / "guard.json"

    def doc(self, name):
        return next(d for d in self.docs if d.name == name)

    def open(self, name, **kw):
        self.docs.append(FakeDoc(self, name, **kw))
        self.active = self.docs[-1]
        return self.active

    @property
    def documents(self):
        return FakeDocuments(self)

    @property
    def activeDocument(self):
        return self.active

    async def guard(self, plan):
        self.checks.append(plan)
        if self.slow:
            await asyncio.sleep(self.slow)
        if self.broken:
            return "Error: something unexpected"
        FAKE_APP[0] = self
        script = {}
        exec(fg.guard_script(plan, self.result), script)
        script["run"]("")
        return json.loads(self.result.read_text())


FAKE_APP = [None]
_adsk, _core = types.ModuleType("adsk"), types.ModuleType("adsk.core")
_core.Application = types.SimpleNamespace(get=lambda: FAKE_APP[0])
_core.DocumentTypes = types.SimpleNamespace(FusionDesignDocumentType=0)
_fusion_module = types.ModuleType("adsk.fusion")
_fusion_module.Design = types.SimpleNamespace(cast=lambda product: product)
_adsk.core, _adsk.fusion = _core, _fusion_module
sys.modules.setdefault("adsk", _adsk)
sys.modules.setdefault("adsk.core", _core)
sys.modules.setdefault("adsk.fusion", _fusion_module)

EXEC = "mcp__fusion360__fusion_mcp_execute"


def script(text, read_only=False):
    return {"featureType": "script", "object": {"script": text, "readOnly": read_only}}


async def call(gate, session, args=None, effect=None, kind=None, timeout=1):
    """One Fusion call through the gate, as the hook sends it. `effect` is what the call does in Fusion."""
    args = args or script("def run(_context: str):\n    pass\n")
    kind = kind or fg.classify(EXEC, args)
    name = next(ids)
    why = await gate.admit(session, kind, timeout, call=name, tool=EXEC, args=args)
    if why is None:
        if effect:
            effect(gate.guard.__self__)
        gate.done(name)
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


def test_undeclared_session_holds_fusion_alone():
    async def go():
        gate = fg.Gate(FAST)
        await declare(gate, "a", "water_box")  # e.g. idle, waiting for Cal
        assert await through(gate, "plain") == "busy"  # it could touch any document, water_box too
        gate.release("a", "test")
        assert await through(gate, "plain") is None
        assert await declare(gate, "b", "fan_shroud") == "busy"  # and declared sessions wait for it
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
        fusion = FakeFusion("water_box v3", "fan_shroud v2", active="water_box v3")
        gate = fg.Gate(FAST, guard=fusion.guard)
        await declare(gate, "a", "water_box")
        await declare(gate, "b", "fan_shroud")
        seen = []
        for session in "abab":
            await call(gate, session, effect=lambda f: seen.append(f.active.name))
        assert seen == ["water_box v3", "fan_shroud v2"] * 2
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
        assert body["ok"] is False and fg.NOT_RUN in body["text"]
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
        assert fg.NOT_RUN in body["text"]
    run(go())


def test_command_survives_the_service_restarting_mid_request(monkeypatch):
    import http.client

    replies = iter([http.client.RemoteDisconnected("gone"), {"ok": True, "text": "queued"}])

    def post(payload, timeout):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(fg, "_post", post)
    monkeypatch.setattr(fg, "_start_service", lambda: None)
    assert fg.call({"op": "acquire"}, 1)["text"] == "queued"


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


# --- no call runs in another holder's document --------------------------------------------

RACK, SEATING = "roof_rack_custom_solar_rails_v3 v18", "assy_seating v4"


def rack_and_seating():
    """Fusion with the rack active and the seating open; Cal's notifications go to fusion.checks."""
    fusion = FakeFusion(RACK, SEATING, active=RACK)
    gate = fg.Gate(FAST, guard=fusion.guard, notify=lambda text: fusion.checks.append(text))
    return fusion, gate


def test_sharing_holders_call_runs_only_once_its_own_document_is_active():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        fusion.active = fusion.doc(RACK)  # A's call left its document active
        seen = []
        assert await call(gate, "b", effect=lambda f: seen.append(f.active.name)) is None
        assert seen == [SEATING]
        assert await call(gate, "a", effect=lambda f: seen.append(f.active.name)) is None
        assert seen == [SEATING, RACK]
    run(go())


def test_sharing_holders_call_is_refused_when_its_document_cannot_be_made_active():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        fusion.active = fusion.doc(RACK)
        fusion.doc(SEATING).stuck = True  # activate() does nothing
        ran = []
        async with TestClient(TestServer(fg.make_app(gate))) as client:
            r = await client.post("/", json={"op": "admit", "session": "b", "kind": "write", "call": "x",
                                             "tool": EXEC, "input": script("print(1)")})
            body = await r.json()
        assert body["ok"] is False and fg.NOT_RUN in body["text"]
        assert not gate.calls and "b" in gate.leases  # nothing is left running, and b keeps its lease
        assert await call(gate, "b", effect=ran.append) == "unsafe" and not ran
    run(go())


def test_call_is_refused_when_fusion_cannot_say_which_document_is_active():
    async def go():
        fusion, gate = rack_and_seating()
        gate.cfg = dataclasses.replace(FAST, switch_wait=0.05)
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        fusion.broken = True
        assert await call(gate, "b") == "unsafe"
        assert gate.refused["b"].code == "no_answer"
        fusion.broken, fusion.slow = False, 0.2
        assert await call(gate, "b") == "unsafe"
        assert gate.refused["b"].code == "no_answer"
        assert gate.calls  # the gate's own check still counts as Fusion's running call
        assert await call(gate, "a", timeout=0.05) == "busy"
        await asyncio.sleep(0.25)
        assert not gate.calls
    run(go())


def test_declared_holders_in_different_documents_still_interleave():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        for session in "abba":
            assert await call(gate, session) is None
        assert set(gate.leases) == {"a", "b"}
    run(go())


def test_documents_a_call_creates_become_its_own():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        await call(gate, "b", effect=lambda f: f.open("strut_gas_300mm_body v1"))
        seen = []
        await call(gate, "a", effect=lambda f: seen.append(f.active.name))
        assert seen == [RACK]
        assert "strut_gas_300mm_body" in gate.documents("b")
        assert await declare(gate, "c", "strut_gas_300mm_body") == "busy"  # not shareable by anyone else
        await call(gate, "b", effect=lambda f: seen.append(f.active.name))
        assert seen == [RACK, "strut_gas_300mm_body v1"]  # b goes back to where it was working
    run(go())


def test_new_document_is_not_given_away_once_cal_has_touched_fusion():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        await call(gate, "b", effect=lambda f: f.open("cal_sketch"))
        gate.last_cal_input = time.monotonic()  # Cal may have opened it
        await call(gate, "a")
        assert "cal_sketch" not in gate.documents("b")
    run(go())


def test_holder_whose_documents_are_not_open_starts_in_a_new_empty_design():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        gate.intent["b"] = {"documents": "cushion_dinette"}
        seen = []
        assert await call(gate, "b", effect=lambda f: seen.append(f.active.name)) is None
        assert seen == ["Untitled"] and "untitled" in gate.documents("b")
    run(go())


def test_a_call_that_ends_in_another_holders_document_is_reported():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        await call(gate, "b", effect=lambda f: f.doc(RACK).activate())  # b's script switched to A's document
        await asyncio.sleep(0.01)
        assert RACK in gate.leases["b"].notice and not gate.tripped  # switching alone changed nothing
        seen = []
        await call(gate, "b", effect=lambda f: seen.append(f.active.name))
        assert seen == [SEATING]
    run(go())


def test_two_open_documents_with_one_name_are_refused_rather_than_guessed():
    async def go():
        fusion = FakeFusion("Untitled", "Untitled", "rack v1", active="rack v1")
        gate = fg.Gate(FAST, guard=fusion.guard)
        await declare(gate, "a", "rack")
        await declare(gate, "b", "Untitled")
        assert gate.refused["b"].code == "not_active"
    run(go())


def test_reference_only_copy_is_not_the_one_activated():
    async def go():
        fusion = FakeFusion("rack v1", active="rack v1")
        fusion.docs.insert(0, FakeDoc(fusion, "bracket v2", reference=True))
        fusion.docs.append(FakeDoc(fusion, "bracket v2"))
        gate = fg.Gate(FAST, guard=fusion.guard)
        await declare(gate, "a", "rack")
        await declare(gate, "b", "bracket")
        assert fusion.active is fusion.docs[-1]
    run(go())


# --- nobody else saves a holder's document --------------------------------------------------

def test_script_that_names_another_holders_document_and_saves_is_refused(tmp_path):
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        saving = "for d in app.documents:\n    if d.name.startswith('roof_rack_custom_solar_rails_v3'):\n        d.save('x')\n"
        assert await call(gate, "b", script(saving)) == "unsafe"
        assert gate.refused["b"].code == "names_foreign"
        part = tmp_path / "part.py"
        part.write_text("open_doc('roof_rack_custom_solar_rails_v3')\napp.documents.open(f)\n")
        assert await call(gate, "b", script(f"exec(open('{part}').read())")) == "unsafe"  # read through exec
        reading = "occ = root.occurrences.addByInsert(find('roof_rack_custom_solar_rails_v3'), m, True)\n"
        assert await call(gate, "b", script(reading)) == "unsafe"  # naming it at all is refused
        assert gate.refused["b"].code == "names_foreign"
        assert await call(gate, "b", script("doc.save('assy_seating_v3 notes')\n")) is None
    run(go())


def test_read_only_script_cannot_save_or_close():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "b", "assy_seating")
        assert await call(gate, "b", script("app.activeDocument.save('x')", read_only=True)) == "unsafe"
        assert await call(gate, "b", script("app.activeDocument.close(False)", read_only=True)) == "unsafe"
        writes_a_file = "f = open('/tmp/out.json', 'w')\nf.write(x)\nf.close()\n"
        assert await call(gate, "b", script(writes_a_file, read_only=True)) is None
    run(go())


def test_document_save_saves_your_own_document():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        fusion.active = fusion.doc(RACK)
        saved = []
        save = {"featureType": "document", "object": {"operation": "save"}}
        assert await call(gate, "b", save, effect=lambda f: saved.append(f.active.name)) is None
        assert saved == [SEATING]
    run(go())


def test_protected_document_is_never_saved_by_a_holder_that_did_not_declare_it():
    async def go():
        fusion = FakeFusion("assy_office v9", "pelican v2", active="assy_office v9")
        gate = fg.Gate(FAST, guard=fusion.guard)
        gate.protect("assy_office", "Cal's unsaved changes", False)
        save = {"featureType": "document", "object": {"operation": "save"}}
        assert await call(gate, "plain", save) == "unsafe"  # an undeclared holder, alone in Fusion
        assert gate.refused["plain"].code == "protected"
        assert await call(gate, "plain", script("app.activeDocument.save('x')")) == "unsafe"
        assert await call(gate, "plain", script("print(1)")) is None
        fusion.broken = True
        assert await call(gate, "plain", save) == "unsafe"  # can't tell what's active: no save
        gate.release("plain", "test")
        fusion.broken = False
        gate.protect("pelican", "water box work", False)
        await declare(gate, "w", "pelican")
        assert await call(gate, "w", save) is None  # the agent doing the water box work may save it
    run(go())


def test_holder_cannot_redeclare_into_another_holders_documents():
    async def go():
        gate = fg.Gate(FAST)
        await gate.acquire("a", "rack", None, "rack")
        await gate.acquire("b", "seating", None, "seating")
        await gate.acquire("b", "seating", None, "seating, rack")
        assert gate.documents("b") == {"seating"}
        await gate.acquire("b", "seating", None, "")
        assert gate.documents("b") == {"seating"}
        await gate.acquire("b", "seating", None, "seating, cushion")
        assert gate.documents("b") == {"seating", "cushion"}
    run(go())


def test_effects_read_what_a_call_can_do():
    doc = lambda op: {"featureType": "document", "object": {"operation": op}}
    assert fg.effects(EXEC, doc("save"), "write")[0] == {"save"}
    assert fg.effects(EXEC, doc("close"), "write")[0] == {"close"}
    assert fg.effects(EXEC, doc("open"), "write")[0] == {"switch"}
    assert fg.effects(EXEC, script("d.saveAs(n, f, '', '')\nx = app.documents.add(t)"), "write")[0] == {"save", "switch"}
    assert fg.effects(EXEC, script("f.close()"), "write")[0] == set()
    assert fg.effects(EXEC, None, "write")[0] == {"save", "close", "switch"}  # nothing to read: assume the worst
    assert fg.names_in("x = 'assy_box_water'", ["assy_box"]) == []
    assert fg.names_in("open_document('Assy_Box')", ["assy_box"]) == ["assy_box"]


# --- the hook fails closed -------------------------------------------------------------------

def test_hook_refuses_a_call_it_cannot_check(monkeypatch):
    def broken(payload, timeout):
        raise RuntimeError("service exploded")

    monkeypatch.setattr(fg, "call", broken)
    pre = {"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": EXEC, "tool_input": script("x")}
    assert fg.run_hook(json.dumps(pre))["hookSpecificOutput"]["permissionDecision"] == "deny"
    docs = pre | {"tool_name": "mcp__fusion360__fusion_mcp_read", "tool_input": {"queryType": "apiDocumentation"}}
    assert fg.run_hook(json.dumps(docs)) is None  # never looks at a design
    assert fg.run_hook(json.dumps({"hook_event_name": "PostToolUse", "session_id": "s"})) is None
    assert fg.run_hook("not json")["hookSpecificOutput"]["permissionDecision"] == "deny"
    monkeypatch.setattr(fg, "call", lambda payload, timeout: (_ for _ in ()).throw(TimeoutError("timed out")))
    assert fg.run_hook(json.dumps(pre))["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.skipif(not Path("/usr/bin/python3").exists(), reason="no system python3")
def test_hook_runs_under_the_system_python():
    """The hook is run as `python3`, which on a Mac without Homebrew's is Python 3.9."""
    r = subprocess.run(["/usr/bin/python3", fg.SCRIPT, "hook"], input='{"hook_event_name": "Stop"}',
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


HOOKS = Path(fg.SCRIPT).parent / "hooks"


def test_hook_command_blocks_the_call_if_python_fails():
    hooks = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
    command = hooks["PreToolUse"][0]["hooks"][0]["command"]
    assert command.endswith("|| exit 2")  # exit code 2 is the one that blocks a PreToolUse call
    failing = command.replace('sh "${CLAUDE_PLUGIN_ROOT}/hooks/gate.sh" pre', "false")
    assert subprocess.run(["/bin/sh", "-c", failing]).returncode == 2
    assert fg.ADMIT_TIMEOUT < hooks["PreToolUse"][0]["hooks"][0]["timeout"]


def gate_sh(mode, event, **env):
    return subprocess.run(["/bin/sh", str(HOOKS / "gate.sh"), mode], input=json.dumps(event), capture_output=True,
                          text=True, timeout=120, env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), **env})


@pytest.mark.skipif(not shutil.which("uv"), reason="uv isn't installed")
def test_hook_runs_under_uv():
    r = gate_sh("pre", {"hook_event_name": "Stop"}, PATH=f"{Path(shutil.which('uv')).parent}:/usr/bin:/bin")
    assert r.returncode == 0, r.stderr


def test_hook_without_uv_blocks_fusion_calls_but_never_bash():
    r = gate_sh("pre", {"hook_event_name": "PreToolUse"}, FUSION_GATE_UV_CANDIDATES="/nowhere/uv")
    assert r.returncode == 2 and "uv isn't installed" in r.stderr and fg.NOT_RUN in r.stderr
    gate_command = {"hook_event_name": "PreToolUse", "agent_id": "a1", "tool_input": {"command": "fusion_gate.py release"}}
    assert gate_sh("bash", gate_command, FUSION_GATE_UV_CANDIDATES="/nowhere/uv").returncode == 0
    r = gate_sh("bash", {"tool_input": {"command": "ls"}}, FUSION_GATE_UV_CANDIDATES="/nowhere/uv")
    assert r.returncode == 0 and not r.stderr  # other Bash calls never reach uv


def test_another_agents_untitled_document_is_not_mistaken_for_yours():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        gate.intent["b"] = {"documents": "cushion_dinette"}
        await call(gate, "b")  # b starts in a new empty design, 'Untitled'
        await call(gate, "a", effect=lambda f: f.open("Untitled"))  # and so does something of a's
        ran = []
        assert await call(gate, "b", effect=ran.append) == "unsafe" and not ran
        assert gate.refused["b"].code == "not_active"
    run(go())


def test_holder_pulled_back_to_its_own_document_is_told_why():
    async def go():
        fusion, gate = rack_and_seating()
        fusion.docs.append(FakeDoc(fusion, "assy_main v40"))
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        await call(gate, "b", effect=lambda f: f.doc("assy_main v40").activate())  # already open: not b's
        assert await gate.admit("b", "write", 1, call="next", tool=EXEC, args=script("pass")) is None
        assert fusion.active.name == SEATING
        assert "assy_main v40" in gate.done("next")
    run(go())


def test_turning_sharing_off_keeps_checking_holders_that_already_share():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        gate.cfg = dataclasses.replace(FAST, share=False)
        seen = []
        await call(gate, "b", effect=lambda f: seen.append(f.active.name))
        await call(gate, "a", effect=lambda f: seen.append(f.active.name))
        assert seen == [SEATING, RACK]
    run(go())


# --- a sharing script can't reach other documents, and the tripwire catches what gets through --

def test_sharing_script_may_look_up_its_own_document_by_name():
    """As hailey_part.open_document does, from a module the script imports."""
    build = script("import sys\nsys.path.insert(0, '/parts')\nimport hailey_part\nhailey_part.Part('assy_seating')\n")

    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        assert await call(gate, "b", build) is None
        assert await call(gate, "b", script("for d in app.documents:\n    d.name")) is None
        await call(gate, "b", script("for d in app.documents:\n    d.edit()"), effect=lambda f: f.doc(RACK).edit())
        await asyncio.sleep(0.01)
        assert "b" in gate.tripped  # what it does to another agent's document is what stops it
    run(go())


def test_call_that_changes_another_holders_document_stops_its_session():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        await call(gate, "b", effect=lambda f: f.doc(RACK).edit())  # reached the rack without naming it
        await asyncio.sleep(0.01)  # the after-call check
        assert any(str(c).startswith("STOP") for c in fusion.checks)  # Cal is told at once
        assert await call(gate, "b") == "unsafe" and gate.refused["b"].code == "tripped"
        assert await call(gate, "b", script("print(1)", read_only=True)) is None  # b can still look
        assert await gate.admit("a", "write", 1, call="next", tool=EXEC, args=script("pass")) is None  # a isn't blocked
        assert "roof_rack_custom_solar_rails_v3 changed during" in gate.done("next")  # but is told to check
        gate.release("b", "test")
        assert not gate.tripped  # nothing outlives the lease
    run(go())


def test_tripwire_still_catches_a_change_when_the_after_call_check_fails():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")

        def edit_then_break(f):
            f.doc(RACK).edit()
            f.broken = True

        await call(gate, "b", effect=edit_then_break)
        await asyncio.sleep(0.01)
        assert gate.pending and not gate.tripped  # undecided, not forgotten
        fusion.broken = False
        assert await call(gate, "a") is None  # a's check decides it
        assert "b" in gate.tripped and gate.pending[0] == "a"
    run(go())


def test_release_still_decides_its_last_call():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        assert await gate.admit("b", "write", 1, call="x", tool=EXEC, args=script("pass")) is None
        fusion.doc(RACK).edit()
        gate.calls.pop("x")  # its Post event never came
        gate.release("b", "test")
        await asyncio.sleep(0.01)
        assert any(str(c).startswith("STOP") for c in fusion.checks)
    run(go())


def test_a_document_without_an_earlier_reading_is_never_called_changed():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "b", "assy_seating")
        assert await gate.admit("b", "write", 1, call="x", tool=EXEC, args=script("pass")) is None
        await gate.acquire("a", "rack", None, "roof_rack_custom_solar_rails_v3")  # granted during b's call
        fusion.doc(RACK).edit()  # so the rack had no reading from before b's call
        gate.done("x")
        await asyncio.sleep(0.01)
        assert not gate.tripped and not any(str(c).startswith("STOP") for c in fusion.checks)
    run(go())


def test_a_refused_call_is_not_blamed_for_changes():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        await call(gate, "a")
        assert await call(gate, "b", script("open('roof_rack_custom_solar_rails_v3')")) == "unsafe"
        assert gate.last_session == "a"
    run(go())


def test_own_changes_and_cals_changes_do_not_stop_anyone():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        await call(gate, "b", effect=lambda f: f.doc(SEATING).edit())
        await call(gate, "a", effect=lambda f: f.doc(RACK).edit())
        await asyncio.sleep(0.01)
        assert not any(str(c).startswith("STOP") for c in fusion.checks)

        def cal_edits(f):
            gate.last_cal_input = time.monotonic()
            f.doc(SEATING).edit()

        await call(gate, "a", effect=cal_edits)
        await asyncio.sleep(0.01)
        assert not gate.tripped  # it may have been Cal, so a isn't stopped
        assert any("may have been Cal" in str(c) for c in fusion.checks)  # but Cal hears of it
    run(go())


def test_new_document_stays_its_creators_for_the_lease():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        await call(gate, "b", effect=lambda f: f.open("cushion_dinette").edit())
        await asyncio.sleep(0.01)
        assert await declare(gate, "c", "cushion_dinette") == "busy"
        assert await call(gate, "a", script("x = 'cushion_dinette'")) == "unsafe"
        gate.release("b", "test")
        assert await declare(gate, "c", "cushion_dinette") is None  # after the lease, only protect covers it
    run(go())


def test_a_check_that_crashes_frees_fusion_and_refuses_the_call():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")

        async def explode(plan):
            raise KeyError("open")

        gate.guard = explode
        assert await call(gate, "b") == "unsafe"
        assert not gate.calls
    run(go())


def test_save_and_close_are_seen_through_simple_disguises():
    assert fg.effects(EXEC, script("getattr(doc, 'save')('x')"), "write")[0] == {"save"}
    assert fg.effects(EXEC, script("doc.save ('x')"), "write")[0] == {"save"}
    assert fg.effects(EXEC, script("getattr(doc, \"close\")(False)"), "write")[0] == {"close"}


def test_document_made_by_a_holder_alone_in_fusion_is_still_its_own():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "b", "assy_seating")
        await asyncio.sleep(0.01)  # the first call's after-call check
        checks = len(fusion.checks)
        await call(gate, "b", effect=lambda f: f.open("cushion_dinette").edit())
        await asyncio.sleep(0.01)
        assert len(fusion.checks) == checks  # alone, so its call wasn't checked
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")  # the next check learns what b made
        assert "cushion_dinette" in gate.documents("b")
        assert await call(gate, "b", effect=lambda f: f.active.edit()) is None
        assert fusion.active.name == "cushion_dinette"  # and b is left in it
    run(go())


def test_two_agents_new_untitled_designs_do_not_trip_each_other():
    async def go():
        fusion = FakeFusion(RACK, active=RACK)
        gate = fg.Gate(FAST, guard=fusion.guard)
        await declare(gate, "a", "cushion_dinette")  # starts in a new empty design
        fusion.active.name = "cushion_dinette v1"  # which a's call saves under its name
        await call(gate, "a")
        assert gate.documents("a") == {"cushion_dinette"}
        await declare(gate, "b", "pusher_backrest")  # b's own new 'Untitled'
        await call(gate, "b", effect=lambda f: f.active.edit())
        await asyncio.sleep(0.01)
        assert not gate.tripped
    run(go())


def test_undeclared_call_decides_a_pending_tripwire_first():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        assert await gate.admit("b", "write", 1, call="x", tool=EXEC, args=script("pass")) is None
        fusion.broken = True
        gate.done("x")  # the after-call check fails
        await asyncio.sleep(0.01)
        gate.release("a", "test")
        gate.release("b", "test")  # and so does the one release starts
        await asyncio.sleep(0.01)
        fusion.broken = False
        assert gate.pending
        await call(gate, "plain")  # holds Fusion alone, but its call is checked while one is pending
        assert not gate.pending
    run(go())


# --- subagents, retried checks, stale holders ----------------------------------------------

def test_subagents_hold_fusion_apart_and_the_tripwire_catches_a_sibling():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "s/one", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "s/two", "assy_seating")
        assert set(gate.leases) == {"s/one", "s/two"}
        await call(gate, "s/two", effect=lambda f: f.doc(RACK).edit())
        await asyncio.sleep(0.01)
        assert "s/two" in gate.tripped and "s/one" not in gate.tripped
    run(go())


def test_subagent_works_in_its_parents_documents_without_waiting_for_it():
    async def go():
        fusion, gate = rack_and_seating()
        await declare(gate, "s", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "other", "assy_seating")
        assert await call(gate, "s/builder", effect=lambda f: f.doc(RACK).edit()) is None
        await asyncio.sleep(0.01)
        assert not gate.tripped and fusion.active.name == RACK
        gate.release("s", "test")
        assert gate.documents("s/builder") == {"roof_rack_custom_solar_rails_v3"}  # kept after its parent left
        assert await call(gate, "other") is None
    run(go())


def test_hook_keys_a_subagent_apart_and_main_sessions_as_before(monkeypatch):
    sent = []
    monkeypatch.setattr(fg, "call", lambda payload, timeout: sent.append(payload) or {"ok": True, "text": ""})
    pre = {"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": EXEC, "tool_input": {}, "tool_use_id": "t"}
    fg.hook(pre)
    fg.hook(pre | {"agent_id": "a1", "agent_type": "general-purpose"})
    fg.hook({"hook_event_name": "SubagentStop", "session_id": "s", "agent_id": "a1"})
    assert [p["session"] for p in sent] == ["s", "s/a1", "s/a1"] and sent[-1]["op"] == "forget"


def test_subagents_gate_command_must_say_which_subagent_it_is(monkeypatch):
    monkeypatch.setattr(fg, "call", lambda payload, timeout: pytest.fail("Bash calls never ask the service"))
    bash = {"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Bash", "agent_id": "a1"}
    acquire = bash | {"tool_input": {"command": 'python3 "/x/fusion_gate.py" acquire "rack" -m 5'}}
    refused = fg.hook(acquire)["hookSpecificOutput"]
    assert refused["permissionDecision"] == "deny" and "FUSION_GATE_AGENT=a1" in refused["permissionDecisionReason"]
    assert fg.hook(bash | {"tool_input": {"command": "FUSION_GATE_AGENT=a1 python3 /x/fusion_gate.py release"}}) is None
    assert fg.hook(bash | {"tool_input": {"command": "python3 /x/fusion_gate.py status"}}) is None
    assert fg.hook({**acquire, "agent_id": None}) is None  # the main session
    assert fg.gate_command("s/a1", "release").startswith("FUSION_GATE_AGENT=a1 python3 ")


def test_session_end_forgets_its_subagents():
    async def go():
        gate = fg.Gate(FAST)
        await declare(gate, "s/one", "water_box")
        await declare(gate, "t/one", "fan_shroud")
        gate.forget("s")
        assert set(gate.leases) == {"t/one"}
    run(go())


def test_check_that_comes_back_empty_is_asked_again():
    async def go():
        fusion, gate = rack_and_seating()
        gate.cfg = dataclasses.replace(FAST, check_backoff=0.01)
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")
        await declare(gate, "b", "assy_seating")
        answer = fusion.guard

        async def flaky(plan):
            if not getattr(flaky, "failed", False):
                flaky.failed = True
                return "Error: no result file"
            return await answer(plan)

        gate.guard = flaky
        assert await call(gate, "b") is None
        assert fusion.active.name == SEATING
    run(go())


def test_holder_alone_is_never_refused_for_an_unreadable_check():
    async def go():
        fusion, gate = rack_and_seating()
        fusion.broken = True
        await declare(gate, "b", "assy_seating")
        assert await call(gate, "b") is None
        await declare(gate, "a", "roof_rack_custom_solar_rails_v3")  # not alone any more
        assert await call(gate, "b") == "unsafe" and gate.refused["b"].code == "no_answer"
    run(go())


def test_holder_whose_claude_code_has_exited_is_released():
    dead = subprocess.Popen(["true"])
    dead.wait()

    async def go():
        gate = fg.Gate(FAST)
        await gate.acquire("crashed", "rack", 60, "water_box")
        gate.pids["crashed"] = dead.pid
        assert "STALE" in gate.status("someone") and "exited" in gate.status("someone")
        gate.tick()
        assert not gate.leases
    run(go())


def test_stale_holders_are_shown_and_can_be_freed_by_anyone():
    async def go():
        gate = fg.Gate(dataclasses.replace(FAST, stale_lease=0.05))
        await gate.acquire("1a2b3c4d-crashed", "rack", 60, "water_box")
        await gate.acquire("5e6f-live", "shroud", 60, "fan_shroud")
        await asyncio.sleep(0.1)
        gate.leases["5e6f-live"].last_call = time.monotonic()
        assert "id 1a2b3c4d" in gate.status("x") and "release --stale" in gate.status("x")
        async with TestClient(TestServer(fg.make_app(gate))) as client:
            r = await (await client.post("/", json={"op": "release", "session": "x", "stale": True})).json()
            assert "Freed 1" in r["text"] and set(gate.leases) == {"5e6f-live"}
            r = await (await client.post("/", json={"op": "release", "session": "x", "target": "5e6f"})).json()
            assert r["ok"] and not gate.leases
    run(go())
