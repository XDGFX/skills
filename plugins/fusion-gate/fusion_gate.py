#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.9"]
# ///
"""fusion-gate: agents take turns with Fusion.

Fusion runs every MCP call on its main thread, and two sessions' calls at once can swap each
other's output or land in each other's documents. A hook on every Fusion tool asks this file's
lock service for a turn. The service lends Fusion to one session at a time, or to several that
declared different documents, and lets one call at a time through. Before a sharing session's
call it makes one of that session's documents active and checks it, or refuses the call.

    fusion_gate.py hook                              the plugin's hook (reads the event on stdin)
    fusion_gate.py serve                             the lock service; the hook starts it
    fusion_gate.py acquire REASON [-m MIN] [-d DOCS] wait for a turn and hold Fusion
    fusion_gate.py release [--session ID | --stale]  let go, or free a holder whose session is gone
    fusion_gate.py status | pause | resume
    fusion_gate.py protect DOCUMENT [REASON] [--clear]

A subagent is a holder of its own, keyed by the hook's agent_id; its commands carry
FUSION_GATE_AGENT=<agent_id>. The plugin runs the hook under `uv run --script` (hooks/gate.sh);
the commands need only the standard library and also run on Python 3.9 (macOS's own python3).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

PORT = int(os.environ.get("FUSION_GATE_PORT", "27183"))
FUSION = os.environ.get("FUSION_GATE_FUSION", "http://127.0.0.1:27182/mcp")
SCRIPT = os.path.abspath(__file__)
LOG = Path.home() / "Library" / "Logs" / "fusion-gate.log"
STATE_DIR = Path(os.environ.get("FUSION_GATE_STATE_DIR") or Path.home() / ".cache" / "fusion-gate")


@dataclasses.dataclass
class Config:
    wait_block: float = 30  # a Fusion call waits this long for its turn, then is refused
    holder_wait: float = 55  # a holder's call waits this long for another holder's call,
    switch_wait: float = 20  # then this long for the gate's document check: both under the hook's 85 s
    check_attempts: int = 3  # a check that comes back empty is asked again, within switch_wait
    check_backoff: float = 0.5
    stale_lease: float = 900  # status calls a holder stale after this long without a call
    acquire_wait: float = 100  # under the Bash tool's default 2 min limit
    idle_read: float = 30  # a lease ends after this long without a call
    idle_write: float = 300
    idle_explicit: float = 600  # once the holder has said what it is doing
    ageing: float = 600  # a waiter this old goes to the front
    place_kept: float = 900  # a session that stopped waiting keeps its place this long
    rejoin: float = 60  # and for this long nobody behind it is let in, so a re-run of acquire loses nothing
    share: bool = os.environ.get("FUSION_GATE_SHARE", "1") != "0"  # agents in different documents interleave
    stale_call: float = 75  # Claude Code gives up on a call at 60 s; Fusion may still be running it
    manual_resume: float = 600  # Cal's pause ends after this long without touching Fusion
    auto_resume: float = 180
    auto_pause: bool = os.environ.get("FUSION_GATE_AUTOPAUSE", "1") != "0"
    notify_overrun: bool = os.environ.get("FUSION_GATE_NOTIFY_OVERRUN") == "1"


# fusion_mcp_read queries that never look at the open design
STATELESS_QUERIES = {"apiDocumentation", "licensing", "projects", "document", "activeCommand"}


def classify(tool: str, args: dict) -> str:
    """'stateless' calls skip the lease, 'read' calls look at the design, 'write' may change it."""
    tool = tool.removeprefix("mcp__fusion360__")
    if tool == "fusion_mcp_read":
        return "stateless" if args.get("queryType") in STATELESS_QUERIES else "read"
    if tool == "fusion_mcp_electronics_read":
        return "read"
    obj = args.get("object") or {}
    if tool == "fusion_mcp_execute" and args.get("featureType") == "script" and obj.get("readOnly"):
        return "read"
    return "write"


NOT_RUN = "NOT run"  # agents are told to look for this in a refusal (fusion-modelling's API.md)
SAVES = re.compile(r"""\.\s*save(?:As)?\s*\(|['"]save(?:As)?['"]""")  # also getattr(doc, 'save')
CLOSES = re.compile(r"""\.\s*close\s*\(\s*[^)\s]|['"]close['"]""")  # Document.close takes an argument; a file's doesn't
SWITCHES = re.compile(r"documents\.(?:open|add)\(|\.activate\(")
RUNS_FILE = re.compile(r"""open\(\s*(['"])(/[^'"]+\.py)\1""")  # exec(open('/path/part.py').read())


def script_text(script: str, depth: int = 2) -> str:
    """A script with the files it runs through exec(open(...)), so the gate can read what it does."""
    text = script
    for m in RUNS_FILE.finditer(script) if depth else ():
        try:
            text += "\n" + script_text(Path(m.group(2)).read_text(errors="replace"), depth - 1)
        except OSError:
            pass
    return text


def effects(tool: str, args: dict | None, kind: str) -> tuple[set, str]:
    """What a call may do beyond changing the active design: 'save', 'close' or 'switch' documents.
    Also returns the script text to look for other documents' names in."""
    if args is None:  # nothing to read: assume the worst a call of this kind can do
        return ({"save", "close", "switch"} if kind == "write" else set()), ""
    tool = tool.removeprefix("mcp__fusion360__")
    obj = args.get("object") or {}
    if tool == "fusion_mcp_execute" and args.get("featureType") == "document":
        operation = {"save": "save", "close": "close", "open": "switch"}.get(obj.get("operation"))
        return ({operation} if operation else {"save", "close", "switch"}), ""
    if tool == "fusion_mcp_execute" and args.get("featureType") == "script":
        text = script_text(str(obj.get("script") or ""))
        return {e for e, rx in (("save", SAVES), ("close", CLOSES), ("switch", SWITCHES)) if rx.search(text)}, text
    return set(), ""


def base_name(name: str) -> str:
    """A document's name without Fusion's version, for comparing: 'Rack v20' -> 'rack'."""
    return re.sub(r" v\d+$", "", name.strip()).casefold()


def names_in(text: str, names) -> list[str]:
    """Which of `names` appear in `text` as whole names: assy_box doesn't match assy_box_water."""
    folded = text.casefold()
    return sorted(n for n in names if re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", folded))


def holder_key(session: str, agent: str | None) -> str:
    """Who holds Fusion: a session, or one subagent in it ('session/agent')."""
    return f"{session}/{agent}" if agent else session


def parent_of(key: str | None) -> str | None:
    return key.split("/", 1)[0] if key and "/" in key else None


def family(a: str | None, b: str | None) -> bool:
    """A session and its own subagents: they never wait for, or trip, each other. Siblings do."""
    return a == b or (a is not None and parent_of(a) == b) or (b is not None and parent_of(b) == a)


def short_id(key: str) -> str:
    return "/".join(part[:8] for part in key.split("/"))


def id_matches(key: str, wanted: str) -> bool:
    """Whether `wanted` (a short_id, or the whole key) names `key`."""
    parts, given = key.split("/"), wanted.split("/")
    return len(parts) == len(given) and all(p.startswith(g) for p, g in zip(parts, given))


def gate_command(key: str, rest: str) -> str:
    """The command line a holder runs, carrying a subagent's id so the gate knows who it is."""
    agent = key.split("/", 1)[1] if parent_of(key) else ""
    return f"{f'FUSION_GATE_AGENT={agent} ' if agent else ''}python3 {SCRIPT} {rest}"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def log(text: str) -> None:
    print(time.strftime("%H:%M:%S"), text, file=sys.stderr, flush=True)


def ago(seconds: float) -> str:
    return f"{int(seconds)} s" if seconds < 90 else f"{round(seconds / 60)} min"


@dataclasses.dataclass
class Lease:
    session: str
    idle: float
    since: float
    last_call: float
    reason: str = ""
    documents: str = ""
    minutes: float | None = None
    until: float = 0  # held regardless of calls until then: the estimate from the last acquire
    told: bool = False
    overrun_told: bool = False
    notice: str = ""  # told to the holder when its next call comes back
    owned: list = dataclasses.field(default_factory=list)  # documents its calls created or opened
    active: str = ""  # the document of its own that its last call left active

    @property
    def first_document(self) -> str:
        return self.documents.split(",")[0].strip()


def document_set(documents: str | None) -> frozenset | None:
    """The documents a session declared, or None: it needs all of Fusion."""
    names = frozenset(base_name(d) for d in (documents or "").split(",") if d.strip())
    return names or None


@dataclasses.dataclass(frozen=True)
class Refusal:
    """Why the gate stopped a call: `code` for the gate and its tests, `text` for the agent."""
    code: str
    text: str


@dataclasses.dataclass
class Pause:
    since: float
    auto: bool


class Gate:
    def __init__(self, config: Config | None = None, notify=lambda text: None, probe=None,
                 state: Path | None = None, guard=None):
        self.cfg = config or Config()
        self.notify = notify
        self.probe = probe  # returns once Fusion has finished whatever it is running
        self.guard = guard  # runs GUARD_SCRIPT's plan in Fusion and returns its answer; None in queue tests
        # Holders: several when each declared documents no other holder declared.
        self.leases: dict[str, Lease] = {}
        self.last_session: str | None = None  # whose call Fusion ran last
        self.last_done = 0.0  # when a call last came back
        self.known: set | None = None  # documents open at the last check; None once unknown
        self.checked_at = 0.0  # when the check the gate last learnt from started
        self.this_check = 0.0  # when the latest check started
        self.last_active = ""  # the active document the last check saw
        self.pending: tuple | None = None  # (session, documents read before its write call, when) until decided
        self.refused: dict[str, Refusal] = {}  # session -> why the gate stopped its call
        self.tripped: dict[str, str] = {}  # session whose call changed another's document -> what to tell it
        self.pause: Pause | None = None
        self.down = False
        self.calls: dict[str, tuple[str, float]] = {}  # call in Fusion -> (session, started)
        self.waiting: dict[str, tuple[int, float]] = {}  # session -> (rank, queued since)
        self._refs: dict[str, int] = {}  # concurrent waits per session
        self.places: dict[str, tuple[int, float, float]] = {}  # session -> (rank, queued since, left at)
        self.intent: dict[str, dict] = {}  # session -> what acquire said
        self.where: dict[str, str] = {}  # session -> the project it runs in
        self.pids: dict[str, int] = {}  # session -> its Claude Code process, when the hook was told
        self.state = state  # where protections, holders and the queue survive a restart
        self.protected: dict[str, str] = {}  # document -> why
        if state and state.exists():
            self._restore(json.loads(state.read_text()))
        self.last_cal_input = 0.0
        self._probing = False
        self._changed = asyncio.Event()

    # --- the queue -----------------------------------------------------------------------

    def _poke(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    def _key(self, item, now):
        rank, since = item[1]
        return (0 if now - since >= self.cfg.ageing else rank, since)

    def stepped_away(self) -> dict[str, tuple[int, float]]:
        """Waiters between two acquire runs: nobody behind them is let in meanwhile."""
        now = time.monotonic()
        return {s: (rank, since) for s, (rank, since, left) in self.places.items()
                if now - left < self.cfg.rejoin and s not in self.waiting and s not in self.leases}

    def queue(self) -> list[str]:
        now = time.monotonic()
        waiting = {s: entry for s, entry in self.waiting.items() if s not in self.leases}  # not a holder mid-call
        entries = {**self.stepped_away(), **waiting}
        return [s for s, _ in sorted(entries.items(), key=lambda i: self._key(i, now))]

    def _rank(self, session: str, kind: str) -> int:
        if kind == "read":
            return 0
        minutes = self.intent.get(session, {}).get("minutes")
        return 2 if minutes and minutes > 10 else 1

    def _start(self, call: str | None, session: str) -> None:
        if call:
            self.calls[call] = (session, time.monotonic())

    async def admit(self, session: str, kind: str, timeout: float, call: str | None = None,
                    through_down: bool = False, tool: str = "", args: dict | None = None):
        """Wait for `session`'s turn. With `call`, also wait for Fusion to be idle, record the call
        as running and check it can't reach another holder's documents. Returns None once through,
        otherwise why not: 'down', 'paused', 'busy', or 'unsafe' with the reason in `refused`."""
        if kind == "stateless":
            return await self._wait(timeout, through_down, lambda: self._slot(call, session))
        now = time.monotonic()
        rank = self._rank(session, kind)
        if session in self.waiting:
            rank, since = min(rank, self.waiting[session][0]), self.waiting[session][1]
        else:
            kept_rank, since, left = self.places.pop(session, (rank, now, now))
            rank = min(rank, kept_rank)
            if now - left >= self.cfg.place_kept:
                since = now
        self.waiting[session] = (rank, since)
        self._refs[session] = self._refs.get(session, 0) + 1

        def turn():
            if self.pause or self.down or (call and self.calls):
                return False
            if session not in self.leases and self._may_grant(session):
                self._grant(session)
            lease = self.leases.get(session)
            if not lease:
                return False
            idle = self.cfg.idle_read if kind == "read" else self.cfg.idle_write
            lease.idle = max(lease.idle, idle)
            self._start(call, session)
            return True

        try:
            why = await self._wait(timeout, through_down, turn)
        finally:
            self._refs[session] -= 1
            if not self._refs[session]:
                del self._refs[session]
                rank, since = self.waiting.pop(session)
                if session not in self.leases:
                    self.places[session] = (rank, since, time.monotonic())
                self._poke()
        if why is None and call:
            try:
                unsafe = await self._check(session, kind, tool, args)
            except Exception as e:
                unsafe = Refusal("gate_error", f"the gate failed while checking the call ({type(e).__name__}: {e}).")
            except BaseException:  # cancelled: the call never reaches Fusion, so it mustn't hold the slot
                self.calls.pop(call, None)
                self._poke()
                raise
            if unsafe:
                self.calls.pop(call, None)
                self._poke()
                self.refused[session] = unsafe
                log(f"refused a call by {self.describe(session)}: {unsafe.text}")
                why = "unsafe"
        return why

    def documents(self, session: str) -> frozenset | None:
        """What `session` works in: what it declared and what its calls created or opened. None
        when it declared nothing and so may touch any document."""
        declared = document_set(self.intent.get(session, {}).get("documents"))
        parent = parent_of(session)
        if declared is None and parent in self.leases:
            declared = self.documents(parent)  # a subagent that declared nothing works in its parent's
        lease = self.leases.get(session)
        return declared | frozenset(lease.owned) if declared and lease else declared

    def _clash(self, a: str, b: str) -> bool:
        """Declared sets clash when they overlap. A session that declared nothing could touch any
        document, so it clashes with everyone and holds Fusion alone; so does everyone with sharing off."""
        mine, theirs = self.documents(a), self.documents(b)
        return not self.cfg.share or mine is None or theirs is None or bool(mine & theirs)

    def owner(self, document: str, besides: str | None = None) -> str | None:
        """The holder, outside `besides`'s family, whose documents include `document` (a base name)."""
        return next((s for s in self.leases if not (besides and family(s, besides))
                     and document in (self.documents(s) or ())), None)

    def _protected_for(self, session: str) -> dict[str, str]:
        """Protected documents `session` didn't declare, by base name: it may never save or close them."""
        mine = self.documents(session) or frozenset()
        return {base_name(d): why for d, why in self.protected.items() if base_name(d) not in mine}

    def _may_grant(self, session: str) -> bool:
        """Free of every holder's documents and of every waiter's queued ahead of it."""
        ahead = []
        for other in self.queue():
            if other == session:
                break
            ahead.append(other)
        return not any(self._clash(session, other) for other in [*self.leases, *ahead] if not family(session, other))

    def reserved(self, viewer: str) -> dict[str, str]:
        """Documents other holders declared or created: document -> who holds it."""
        return {d: self.describe(s) for s in self.leases if not family(s, viewer)
                for d in sorted(self.documents(s) or ())}

    def _reserved_line(self, viewer: str) -> str:
        shown = self.reserved(viewer)
        if not shown:
            return ""
        return "\n\nOther agents hold these; don't open, change, save or close them: " + "; ".join(
            f"{d} ({who})" for d, who in shown.items())

    # --- keeping each holder in its own documents ------------------------------------------

    def foreign(self, viewer: str) -> dict[str, str]:
        """Other holders' documents, by base name, and who holds each."""
        return {d: self.describe(s) for s in self.leases if not family(s, viewer) for d in self.documents(s) or ()}

    def _attributable(self, previous: str | None) -> bool:
        """Whether what changed in Fusion since the last check was done by `previous`'s last call:
        it's a sharing holder and Cal hasn't touched Fusion since."""
        return (previous in self.leases and self.documents(previous) is not None
                and self.last_cal_input <= self.checked_at)

    def _tripped(self, session: str, kind: str) -> Refusal | None:
        if kind == "write" and session in self.tripped:
            return Refusal("tripped", self.tripped[session])
        return None

    async def _check(self, session: str, kind: str, tool: str, args: dict | None) -> Refusal | None:
        """Before a call reaches Fusion: put a sharing holder in a document of its own, read the
        active document back, and refuse what could reach another holder's or a protected
        document. Returns why the call is refused, or None."""
        refused = await self._refusal(session, kind, tool, args)
        if not refused:
            self.last_session = session  # only a call that runs can have changed anything
        return refused

    async def _refusal(self, session: str, kind: str, tool: str, args: dict | None) -> Refusal | None:
        previous = self.last_session
        lease = self.leases.get(session)
        if not (self.guard and lease):
            return None
        stopped = self._tripped(session, kind)
        if stopped:
            return stopped
        acts, text = effects(tool, args, kind)
        mine, foreign, protected = self.documents(session), self.foreign(session), self._protected_for(session)
        if kind == "read" and acts & {"save", "close"}:
            return Refusal("read_only_saves",
                           "a read-only script can't save or close a document. Send it without readOnly.")
        named = names_in(text, foreign)
        if named:
            return Refusal("names_foreign", "the script names " + "; ".join(f"{d} ({foreign[d]})" for d in named)
                           + ". Leave other agents' documents alone.")
        named = names_in(text, protected) if acts else []
        if named:
            return Refusal("protected", f"the script can {' and '.join(sorted(acts))} documents and names "
                           + "; ".join(f"{d} (protected: {protected[d]})" for d in named) + ".")
        # Alone with nobody queued there's no one to keep it from. What it opens meanwhile is still
        # learnt: the next check compares against `known`, which the first check set.
        alone = set(self.leases) == {session} and not self.queue()
        saves_protected = bool(acts & {"save", "close"} and protected)
        if mine is None and not saves_protected and not self.pending:
            self.known = None  # an unchecked call may open or create documents
            return None
        if alone and self.known is not None and not saves_protected and not self.pending:
            return None
        plan = self._plan(target=lease.active or lease.first_document, own=mine, others=foreign,
                          claim_new=previous == session and self._attributable(previous)) if mine else self._plan()
        try:
            r = await self._ask_retrying(session, plan)
        except Exception as e:  # slow, down or a strange answer: nothing can be checked, so nothing runs
            self.known = None
            if alone and not saves_protected:
                log(f"document check for {self.describe(session)} failed ({e}); it's alone, so the call runs")
                return None
            why =(f"no answer within {ago(self.cfg.switch_wait)}" if isinstance(e, asyncio.TimeoutError)
                   else str(e) or type(e).__name__)
            return Refusal("no_answer", f"the gate couldn't read which document is active in Fusion ({why}). "
                                        f"Your lease is kept: send the same call again.")
        self._take_in(previous, r)
        stopped = self._tripped(session, kind)  # the tripwire may have just gone off
        if stopped:
            return stopped
        active, here = r.get("active", ""), base_name(r.get("active", ""))
        if r.get("landed") and mine:
            lease.owned.append(here)
            lease.notice = (f"fusion-gate: none of your documents was open, so the gate opened a new "
                            f"empty design ({active}) for your call to start in. Close it when you're done.")
        if r.get("error"):
            return Refusal("not_active", f"the gate couldn't make one of your documents active: {r['error']}.")
        if mine:
            if not here or here not in (self.documents(session) or ()):
                return Refusal("not_active", f"the gate asked Fusion to make {plan['target']} active, but the "
                                             f"active document is {active or 'none'}.")
            left = r.get("replaced", "")
            if previous == session and left and base_name(left) not in self.documents(session) \
                    and not self.owner(base_name(left), besides=session):
                lease.notice = (f"fusion-gate: your last call left {left} active, which isn't one of your "
                                f"documents, so the gate made {active} active for this call. To work in "
                                f"{left}, run acquire again with it added to -d.")
            lease.active = active
        if acts & {"save", "close"} and here in protected:
            return Refusal("protected", f"the active document {active} is protected ({protected[here]}).")
        if mine is None:
            self.known = None  # the call itself isn't watched
        if mine and kind == "write":  # the tripwire's baseline, read before this call
            self.pending = (session, r.get("prints") or {}, self.this_check)
        return None

    def _plan(self, target: str = "", own=(), others=(), claim_new: bool = False) -> dict:
        """What GUARD_SCRIPT does: make `target` active unless one of `own` already is (or, with
        `claim_new`, a document the session's last call created), opening a new empty design when
        none of `own` is open. With no target it only reads."""
        return {"target": target, "own": sorted(own), "others": sorted(others), "claim_new": claim_new,
                "open_blank": bool(target), "known": sorted(self.known) if self.known is not None else None,
                "watch": self._watched()}

    def _watched(self) -> list[str]:
        """The documents the tripwire reads: every holder's."""
        return sorted({d for s in self.leases for d in self.documents(s) or ()})

    def _take_in(self, previous: str | None, r: dict) -> None:
        """Learn from a check what the last call did: whether it changed another holder's
        document, and which documents it opened or created."""
        self._tripwire(r.get("prints"))
        self._attribute(previous, r)
        self.known = {base_name(n) for n in r.get("open_after", r["open"])}
        for lease in self.leases.values():  # one saved under a new name or closed isn't its any more
            lease.owned = [d for d in lease.owned if d in self.known]
        self.last_active = r.get("active", "")
        self.checked_at = self.this_check

    def _attribute(self, previous: str | None, r: dict) -> None:
        """The documents the last call opened or created become its session's own, unless someone
        else has them. A call that left another session's document active is reported."""
        if self.known is None or not self._attributable(previous):
            return
        lease = self.leases[previous]
        left = base_name(r.get("replaced", ""))
        for doc in sorted({base_name(n) for n in r["open"]} - self.known):
            if not self.owner(doc, besides=previous) and doc not in self.documents(previous):
                lease.owned.append(doc)
                log(f"{self.describe(previous)} now also holds {doc}, which its call opened or created")
        other = self.owner(left, besides=previous) if left and r.get("replaced") != self.last_active else None
        if other:
            log(f"{self.describe(previous)}'s last call left {r['replaced']} active, a document of "
                f"{self.describe(other)}'s")
            lease.notice = (f"fusion-gate: your last call left {r['replaced']} active, which another agent "
                            f"holds. Don't open or change it.")
        elif left in self.documents(previous):
            lease.active = r["replaced"]

    def _tripwire(self, prints: dict | None) -> None:
        """Decide the pending call: compare each document read before it with the same document
        now. A changed one that another holder holds was changed by that call (or by Cal)."""
        if not self.pending or prints is None:
            return
        culprit, before, since = self.pending
        self.pending = None
        theirs = self.documents(culprit) or () if culprit in self.leases else ()
        hit = {d: self.owner(d, besides=culprit) for d in before if d not in theirs  # same name: can't tell
               if not self._unchanged(before[d], prints.get(d, []))}
        hit = {d: who for d, who in hit.items() if who}
        if not hit:
            return
        cal = self.last_cal_input > since
        text = (f"{', '.join(hit)} changed during {self.describe(culprit)}'s call"
                + (" (Cal used Fusion meanwhile, so it may have been Cal)" if cal else ""))
        log(f"TRIPWIRE: {text}")
        self.notify(f"STOP: {text}. Check before anyone saves it.")
        for doc, who in hit.items():
            owner = self.leases[who]
            owner.notice = "\n\n".join(filter(None, [owner.notice, (
                f"fusion-gate: your document {doc} changed during {self.describe(culprit)}'s call. "
                f"Check it before saving, and tell Cal if it isn't what you left.")]))
        if not cal and culprit in self.leases:
            self.tripped[culprit] = (f"your call changed another agent's document ({', '.join(hit)}), so the "
                                     f"gate refuses your write calls until you release. Stop and tell Cal.")

    @staticmethod
    def _unchanged(before: list, now: list) -> bool:
        """Every copy read before reads the same, and any copy opened since is unmodified."""
        now = list(now)
        for p in before:
            if p not in now:
                return False
            now.remove(p)
        return not any(p[1] for p in now)

    def _settle(self) -> None:
        """Decide a pending call now if Fusion is free; otherwise the next check decides it."""
        if not (self.guard and self.pending) or self.calls:
            return
        culprit = self.pending[0]
        task = self._gate_call(f"after {self.describe(culprit)}'s call", self._plan())
        asyncio.ensure_future(self._after(culprit, task))

    def _gate_call(self, label: str, plan: dict) -> asyncio.Future:
        """Start the gate's own call. It counts as Fusion's running call until it comes back, even
        when the holder's call is refused for taking too long."""
        key = f"(the gate's document check {label})"
        self.calls[key] = ("", time.monotonic())
        self.this_check = time.monotonic()
        task = asyncio.ensure_future(self.guard(plan))

        def finished(_):
            self.calls.pop(key, None)
            self._poke()

        task.add_done_callback(finished)
        return task

    async def _ask_retrying(self, session: str, plan: dict) -> dict:
        """Fusion's answer to the check, asking again after a missing or garbled one. A timeout isn't
        retried: the check is still running in Fusion, and another would only queue behind it."""
        deadline = time.monotonic() + self.cfg.switch_wait
        for attempt in range(self.cfg.check_attempts):
            try:
                return await self._ask_fusion(session, plan, timeout=max(0.01, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                raise
            except Exception as e:
                pause = self.cfg.check_backoff * 2 ** attempt
                if attempt + 1 == self.cfg.check_attempts or time.monotonic() + pause >= deadline:
                    raise
                log(f"document check for {self.describe(session)} failed ({e}); asking again")
                await asyncio.sleep(pause)
                plan = plan | {"open_blank": False}  # the first try may already have opened one
        raise RuntimeError("no attempts")  # check_attempts is at least 1

    async def _ask_fusion(self, session: str, plan: dict, task: asyncio.Future | None = None,
                          timeout: float | None = None) -> dict:
        task = task or self._gate_call(f"for {self.describe(session)}", plan)
        r = await asyncio.wait_for(asyncio.shield(task), self.cfg.switch_wait if timeout is None else timeout)
        if not isinstance(r, dict) or not isinstance(r.get("open"), list):
            raise ValueError(f"unexpected answer {str(r)[:200]}")
        return r

    async def _after(self, session: str, task: asyncio.Future) -> None:
        try:
            self._take_in(session, await self._ask_fusion(session, {}, task))
        except Exception as e:  # the baseline stays pending, so the next check decides instead
            log(f"after-call check for {self.describe(session)} failed: {e}")

    def _slot(self, call, session) -> bool:
        if self.down or self.calls:
            return False
        self._start(call, session)
        return True

    async def _wait(self, timeout, through_down, ready):
        deadline = time.monotonic() + timeout
        while True:
            if self.down and not through_down:
                return "down"
            if ready():
                return None
            left = deadline - time.monotonic()
            if left <= 0:
                return "down" if self.down else "paused" if self.pause else "busy"
            try:
                await asyncio.wait_for(self._changed.wait(), left)
            except TimeoutError:
                pass

    def done(self, call: str) -> str:
        """A call has come back. Returns what the holder should be told on its first call."""
        session, _ = self.calls.pop(call, (None, 0))
        lease = self.leases.get(session)
        self.last_done = time.monotonic()
        self._settle()
        self._poke()
        if not lease:
            return ""
        lease.last_call = time.monotonic()
        notice, lease.notice = lease.notice, ""
        if lease.told:
            return notice
        lease.told = True
        return "\n\n".join(filter(None, [self.welcome(lease), notice]))

    def welcome(self, lease: Lease) -> str:
        text = (f"fusion-gate: you hold Fusion until you run `{gate_command(lease.session, 'release')}` or make "
                f"no Fusion call for {ago(lease.idle)}. ")
        if self.documents(lease.session) and self.cfg.share:
            text += (f"You share Fusion with agents working in other documents, so their calls may run between "
                     f"yours. While they do, before each of your calls the gate makes one of your documents "
                     f"active (the one you last used), reads app.activeDocument back, and refuses the call if "
                     f"it isn't yours; a refusal keeps your lease, so send the call again. "
                     f"Documents your calls create or open become yours until you release; save a new one "
                     f"under its name straight away. Work only in the documents you declared "
                     f"({lease.documents}); to add one, run acquire again with the "
                     f"longer -d list. A script may find one of yours by name in app.documents; one that "
                     f"names another agent's document is refused, and if your call changes another agent's "
                     f"document, the gate stops you.")
        elif self.cfg.share:
            text += ("You declared no documents, so you hold all of Fusion and other agents wait. To share, "
                     "release and acquire again with -d listing every document you'll open or change.")
        return text + self._reserved_line(lease.session) + self._protected_line(lease.session)

    def _grant(self, session: str) -> None:
        now = time.monotonic()
        documents = self.intent.get(session, {}).get("documents") or ""
        parent = parent_of(session)
        if not documents and parent in self.leases and self.documents(parent):
            # Kept as the subagent's own, so it stays in them after its parent lets go.
            documents = ", ".join(sorted(self.documents(parent)))
            self.intent.setdefault(session, {})["documents"] = documents
        self.leases[session] = Lease(session, idle=self.cfg.idle_read, since=now, last_call=now, documents=documents)
        self.places.pop(session, None)
        log(f"Fusion → {self.describe(session)}")

    def release(self, session: str, why: str) -> bool:
        if session not in self.leases:
            return False
        log(f"{self.describe(session)} let go of Fusion: {why}")
        if self.pending and self.pending[0] == session:
            self._settle()  # its last call is still compared with what it found
        del self.leases[session]
        self.refused.pop(session, None)
        self.tripped.pop(session, None)
        self._poke()
        return True

    def forget(self, session: str) -> None:
        """A session or subagent ended; a session takes its subagents with it."""
        for key in {session, *self.leases, *self.places, *self.intent}:
            if key == session or parent_of(key) == session:
                self.release(key, "its session ended")
                self.places.pop(key, None)
                self.intent.pop(key, None)
                self.pids.pop(key, None)

    def stale(self, session: str) -> str:
        """Why a holder looks abandoned, or ''."""
        pid = self.pids.get(session)
        if pid and not alive(pid):
            return "its Claude Code process has exited"
        idle = time.monotonic() - self.leases[session].last_call
        if not self.busy(session) and idle >= self.cfg.stale_lease:
            return f"no call for {ago(idle)}"
        return ""

    def release_stale(self, by: str) -> list[str]:
        gone = [s for s in list(self.leases) if self.stale(s)]
        for s in gone:
            self.release(s, f"stale ({self.stale(s)}), freed by {self.describe(by)}")
        return gone

    def busy(self, session: str) -> bool:
        return any(s == session for s, _ in self.calls.values())

    def tick(self) -> None:
        now = time.monotonic()
        for lease in list(self.leases.values()):
            pid = self.pids.get(lease.session)
            if pid and not alive(pid) and not self.busy(lease.session):
                self.release(lease.session, "its Claude Code process has exited")
                continue
            # An acquire's estimate holds the lease even without calls the hook can see.
            if now >= lease.until and not self.busy(lease.session) and not self.pause and now - lease.last_call > lease.idle:
                self.release(lease.session, f"no call for {ago(now - lease.last_call)}")
            if (self.cfg.notify_overrun and lease.minutes and not lease.overrun_told
                    and now - lease.since > 1.5 * lease.minutes * 60):
                lease.overrun_told = True
                self.notify(f"{self.describe(lease.session)} has held Fusion for {ago(now - lease.since)}, "
                            f"over its {lease.minutes:g} min estimate.")
        if self.probe and not self._probing and any(now - t > self.cfg.stale_call for _, t in self.calls.values()):
            self._probing = True
            asyncio.ensure_future(self._clear_stale(now))
        for session, (_, _, left) in list(self.places.items()):
            if now - left >= self.cfg.place_kept:
                del self.places[session]
        if self.waiting and self.places:
            self._poke()  # a waiter that stepped away may have run out of time to come back
        self.save()

    async def _clear_stale(self, now: float) -> None:
        """The agent gave up on a call but Fusion is still running it: wait for Fusion to finish."""
        try:
            await self.probe()
            for call, (_, started) in list(self.calls.items()):
                if started < now:
                    del self.calls[call]
        except Exception as e:  # Fusion down: the port watcher handles it
            log(f"probe failed: {e}")
        finally:
            self._probing = False
            self._poke()

    # --- Fusion's state and Cal ---------------------------------------------------------

    def set_down(self, down: bool) -> None:
        if down == self.down:
            return
        self.down = down
        if down:
            self.leases.clear()
            self.tripped.clear()
            self.known = self.pending = None
            self.last_session = None
            self.calls.clear()
            log("Fusion is unreachable")
            self.notify("Fusion stopped responding. Agents have been told to stop and wait.")
        else:
            log("Fusion is back")
        self._poke()

    def set_pause(self, auto: bool) -> None:
        if self.pause:
            return
        self.pause = Pause(time.monotonic(), auto)
        self.last_cal_input = time.monotonic()
        log(f"paused ({'Cal is using Fusion' if auto else 'by Cal'})")
        self._poke()

    def resume(self, why: str) -> None:
        if not self.pause:
            return
        self.pause = None
        self.known = None  # Cal may have opened or switched documents
        for lease in self.leases.values():
            lease.last_call = time.monotonic()
        log(f"resumed: {why}")
        self._poke()

    async def acquire(self, session: str, reason: str, minutes: float | None, documents: str) -> str:
        lease = self.leases.get(session)
        if lease and self.cfg.share:  # a holder changing its list mustn't take what others hold
            wanted = document_set(documents)
            wanted = wanted | frozenset(lease.owned) if wanted else None
            clash = [s for s in self.leases if s != session and (
                wanted is None or self.documents(s) is None or wanted & self.documents(s))]
            if clash:
                return (f"Not changed: you still hold {lease.documents or 'all of Fusion'}, and "
                        f"{documents or 'declaring nothing (all of Fusion)'} would overlap what "
                        f"{', '.join(self.describe(s) for s in clash)} holds. Release first and acquire again "
                        f"to wait for it.\n\n{self.status(session)}")
        self.intent[session] = {"reason": reason, "minutes": minutes, "documents": documents}
        why = await self.admit(session, "write", self.cfg.acquire_wait, through_down=True)
        if why:
            queue = self.queue()
            place = queue.index(session) + 1 if session in queue else None
            return (f"Not your turn yet{f' (you are #{place})' if place else ''}; your place is kept. "
                    f"Run the same command again.\n\n{self.status(session)}")
        lease = self.leases[session]
        lease.reason, lease.minutes, lease.documents = reason, minutes, documents
        lease.until = time.monotonic() + (minutes or 0) * 60
        lease.idle = max(lease.idle, self.cfg.idle_explicit)
        lease.last_call = time.monotonic()
        lease.told = True
        return self.welcome(lease)

    # --- what agents are told ------------------------------------------------------------

    def describe(self, session: str, viewer: str | None = None) -> str:
        if session == viewer:
            return "you"
        where = self.where.get(session)
        reason = self.intent.get(session, {}).get("reason")
        sub = parent_of(session)
        name = (f"a subagent in {where}" if where else f"subagent {short_id(session)}") if sub else (
            f"an agent in {where}" if where else f"agent {session[:4]}")
        return f"{name} ({reason})" if reason else name

    def _protected_line(self, viewer: str | None = None) -> str:
        own = (self.documents(viewer) or frozenset()) if viewer in self.leases else frozenset()
        shown = {d: why for d, why in self.protected.items() if base_name(d) not in own}
        if not shown:
            return ""
        return "\n\nNever save or close: " + "; ".join(f"{d} ({why})" for d, why in shown.items())

    def protect(self, document: str, reason: str, clear: bool) -> None:
        if clear:
            self.protected.pop(document, None)
        else:
            self.protected[document] = reason or "protected"
        self.save()

    def save(self) -> None:
        """Write what a restart must not lose. Times are stored as wall-clock times."""
        if not self.state:
            return
        now, shift = time.time(), time.time() - time.monotonic()
        leases = [dataclasses.asdict(lease) | {"since": lease.since + shift, "last_call": lease.last_call + shift,
                                               "until": lease.until + shift if lease.until else 0}
                  for lease in self.leases.values()]
        places = {s: (rank, since + shift, left + shift) for s, (rank, since, left) in self.places.items()}
        places |= {s: (rank, since + shift, now) for s, (rank, since) in self.waiting.items()
                   if s not in self.leases}  # a holder mid-call isn't waiting
        data = {"protected": self.protected, "leases": leases, "places": places,
                "intent": self.intent, "where": self.where, "pids": self.pids}
        self.state.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self.state)

    def _restore(self, data: dict) -> None:
        shift = time.time() - time.monotonic()
        self.protected = data.get("protected", {})
        self.intent = data.get("intent", {})
        self.where = data.get("where", {})
        self.pids = data.get("pids", {})
        for fields in data.get("leases", []):
            lease = Lease(**fields)
            lease.since -= shift
            lease.last_call -= shift
            lease.until = lease.until - shift if lease.until else 0
            self.leases[lease.session] = lease
        # Those waiting when the gate stopped come back as between retries: their place is kept.
        self.places = {s: (rank, since - shift, left - shift) for s, (rank, since, left) in data.get("places", {}).items()}

    def status(self, viewer: str | None = None) -> str:
        now = time.monotonic()
        lines = []
        if self.down:
            lines.append(f"Fusion isn't reachable at {FUSION}: it may have crashed or been closed.")
        if self.pause:
            limit = self.cfg.auto_resume if self.pause.auto else self.cfg.manual_resume
            who = "Cal is using Fusion" if self.pause.auto else "Cal has taken over Fusion"
            lines.append(f"Paused for {ago(now - self.pause.since)}: {who}. Agents resume once Cal "
                         f"hasn't touched Fusion for {ago(limit)}.")
        for lease in self.leases.values():
            parts = [f"Held by {self.describe(lease.session, viewer)} for {ago(now - lease.since)}"]
            parts.append(f"documents: {lease.documents}" if self.documents(lease.session) else "all of Fusion")
            if lease.owned and self.documents(lease.session):
                parts.append(f"and from its calls: {', '.join(lease.owned)}")
            if lease.minutes:
                parts.append(f"expects {lease.minutes:g} min")
            parts.append("a call is running" if self.busy(lease.session) else f"last call {ago(now - lease.last_call)} ago")
            why = self.stale(lease.session)
            if why:
                parts.append(f"looks STALE ({why}; id {short_id(lease.session)})")
            lines.append(", ".join(parts) + ".")
        if any(self.stale(s) for s in self.leases):
            lines.append(f"A stale holder whose session is gone can be freed with `python3 {SCRIPT} release "
                         f"--stale`, or one at a time with `release --session <id>`.")
        if self.calls:
            oldest = min(started for _, started in self.calls.values())
            lines.append(f"Fusion has been running a call for {ago(now - oldest)}; every other call waits until it "
                         f"finishes. Don't send more meanwhile: they queue inside Fusion.")
        elif not self.leases and not self.down and not self.pause:
            lines.append("Fusion is free.")
        queue, away = self.queue(), self.stepped_away()
        if queue:
            names = [self.describe(s, viewer) + (" (between retries)" if s in away else "") for s in queue]
            lines.append("Waiting: " + ", ".join(f"{i}. {q}" for i, q in enumerate(names, 1)))
        return "\n".join(lines) + (self._reserved_line(viewer) if viewer in self.leases else "") + self._protected_line(viewer)


def refusal(gate: Gate, why: str, session: str) -> str:
    head = {
        "busy": "Fusion is in use by another agent",
        "behind": "You still hold Fusion, but another agent's call has been running all this time",
        "paused": "Cal has taken over Fusion",
        "down": "Fusion isn't reachable; it may have crashed or been closed",
        "unsafe": "fusion-gate stopped this call before it reached Fusion",
    }[why]
    if why == "unsafe":
        unsafe = gate.refused.pop(session, None)
        return (f"{head}, so it was {NOT_RUN}: {unsafe.text if unsafe else 'it could reach another document.'}"
                f"\n\nYour lease is kept. Don't work around the gate; if this looks wrong, tell Cal."
                f"\n\n{gate.status(session)}")
    if why == "behind":
        return (f"{head}, so your call was {NOT_RUN}. Your lease is kept: send the same call again."
                f"\n\n{gate.status(session)}")
    if why == "down":
        tail = "Don't retry in a loop. Tell Cal, or wait for Fusion with the command below."
    else:
        tail = "Don't message other agents about it. Wait your turn with the command below, or do other work and try later."
    command = (gate_command(session, 'acquire "<what you are doing>" -m <minutes>') + "   "
               f"(waits up to {ago(gate.cfg.acquire_wait)} and keeps your place; run it again if still queued)")
    return f"{head}, so your call was {NOT_RUN}.\n\n{gate.status(session)}\n\n{tail}\n{command}"


# --- the service ------------------------------------------------------------------------


# The gate's own call before a sharing holder's call. It reports what was active and open, makes
# one of the holder's documents active unless one already is, and reads the active document back.
# The result goes to a file: the Fusion MCP often returns a script's printed output as "".
GUARD_SCRIPT = r"""
import json
import re
import adsk.core
import adsk.fusion

PLAN = json.loads(%r)
RESULT = %r


def base(name):
    return re.sub(r' v\d+$', '', name.strip()).casefold()


def fingerprint(doc):
    try:
        design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
        timeline = design.timeline
        return [doc.name, bool(doc.isModified), timeline.count, timeline.markerPosition,
                design.userParameters.count, design.rootComponent.occurrences.count]
    except Exception as e:  # a copy open only as a reference has no design to read
        return [doc.name, bool(getattr(doc, 'isModified', True)), type(e).__name__]


def fingerprints(docs):
    prints = {}
    for d in docs:
        if base(d.name) in PLAN['watch']:
            prints.setdefault(base(d.name), []).append(fingerprint(d))
    return {b: sorted(p, key=str) for b, p in prints.items()}


def usable(doc):
    try:
        doc.documentReferences.count  # raises for a copy open only as another design's reference
        return True
    except RuntimeError:
        return False


def run(_context: str):
    app = adsk.core.Application.get()
    docs = list(app.documents)
    active = app.activeDocument
    out = {'nonce': PLAN.get('nonce'), 'replaced': active.name if active else '', 'open': [d.name for d in docs],
           'landed': False, 'error': '', 'prints': fingerprints(docs)}
    here = base(active.name) if active else None
    new = PLAN['known'] is not None and here not in PLAN['known'] and here not in PLAN['others']
    unique = sum(base(d.name) == here for d in docs) == 1  # two 'Untitled' can't tell whose is whose
    stay = here is not None and unique and (here in PLAN['own'] or (PLAN['claim_new'] and new))
    target = PLAN['target']
    if target and not stay:
        found = [d for d in docs if d.name == target] or [d for d in docs if base(d.name) == base(target)]
        if len(found) > 1:
            found = [d for d in found if usable(d)]
        if not found:
            found = [d for d in docs if base(d.name) in PLAN['own'] and usable(d)][:1]
        if len(found) > 1:
            out['error'] = 'more than one open document is called ' + target
        elif found:
            found[0].activate()
        elif PLAN['open_blank']:
            app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
            out['landed'] = True
    active = app.activeDocument
    out['active'] = active.name if active else ''
    out['open_after'] = [d.name for d in app.documents]
    with open(RESULT, 'w') as f:
        json.dump(out, f)
"""
GUARD_RESULT = STATE_DIR / f"guard-{PORT}.json"


def guard_script(plan: dict, result: Path) -> str:
    return GUARD_SCRIPT % (json.dumps(plan), str(result))


class Fusion:
    """Fusion's own MCP server, for the gate's own calls: waiting for Fusion and switching documents."""

    def __init__(self, url: str):
        self.url = url

    async def _post(self, http, msg: dict, sid: str | None = None):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if sid:
            headers["Mcp-Session-Id"] = sid
        async with http.post(self.url, json=msg, headers=headers) as r:
            return r.headers.get("Mcp-Session-Id"), await r.text()

    async def _tool(self, name: str, arguments: dict) -> str:
        import aiohttp

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=5)) as http:
            hello = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fusion-gate", "version": "1"}}
            sid, _ = await self._post(http, {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": hello})
            await self._post(http, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
            _, text = await self._post(http, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                              "params": {"name": name, "arguments": arguments}}, sid)
            return text

    async def wait_idle(self) -> None:
        # Any tool call queues behind whatever Fusion is running; this one is cheap.
        await self._tool("fusion_mcp_read", {"queryType": "apiDocumentation", "searchPattern": "^Application$",
                                             "apiCategory": "class"})

    async def guard(self, plan: dict) -> dict:
        """Run GUARD_SCRIPT with `plan`; raises when Fusion gives no usable answer."""
        started = time.monotonic()
        plan = plan | {"nonce": os.urandom(8).hex()}
        GUARD_RESULT.parent.mkdir(parents=True, exist_ok=True)
        GUARD_RESULT.unlink(missing_ok=True)
        text = await self._tool("fusion_mcp_execute", {"featureType": "script", "object": {
            "script": guard_script(plan, GUARD_RESULT)}})
        try:
            result = json.loads(GUARD_RESULT.read_text())
        except (OSError, ValueError):
            log(f"document check: no result; Fusion said {text[:300]}")
            raise RuntimeError("Fusion didn't report the active document") from None
        if result.get("nonce") != plan["nonce"]:
            raise RuntimeError("Fusion's answer was from an earlier check")
        log(f"document check for {plan['target'] or 'watched documents'}: was {result['replaced']!r}, now "
            f"{result['active']!r}{', opened a new design' if result['landed'] else ''}"
            f"{', ' + result['error'] if result['error'] else ''}, {time.monotonic() - started:.1f} s")
        return result


GATE = None  # aiohttp's AppKey, made with the app so the hook never imports aiohttp


async def handle(request):
    from aiohttp import web

    gate: Gate = request.app[GATE]
    body = await request.json()
    op, session = body.get("op"), body.get("session") or "unknown"
    if body.get("where"):
        gate.where.setdefault(session, body["where"])
    if isinstance(body.get("pid"), int):
        gate.pids[session] = body["pid"]
    ok, text = True, ""
    if op == "admit":
        holds = session in gate.leases
        why = await gate.admit(session, body["kind"], gate.cfg.holder_wait if holds else gate.cfg.wait_block,
                               call=body.get("call"), tool=body.get("tool") or "", args=body.get("input"))
        if why == "busy" and session in gate.leases:
            why = "behind"
        ok, text = why is None, refusal(gate, why, session) if why else ""
    elif op == "done":
        text = gate.done(body.get("call", ""))
    elif op == "acquire":
        text = await gate.acquire(session, body.get("reason", ""), body.get("minutes"), body.get("documents", ""))
    elif op == "release" and body.get("stale"):
        gone = gate.release_stale(session)
        text = (f"Freed {len(gone)} stale holder{'s' * (len(gone) != 1)}." if gone else "No holder looks stale.") \
            + f"\n\n{gate.status(session)}"
    elif op == "release" and body.get("target"):
        found = [s for s in gate.leases if id_matches(s, body["target"])]
        if len(found) == 1:
            gate.release(found[0], f"freed by {gate.describe(session)}")
            text = f"Freed {short_id(found[0])}.\n\n{gate.status(session)}"
        else:
            ok, text = False, (f"{'More than one holder matches' if found else 'No holder matches'} "
                               f"{body['target']}.\n\n{gate.status(session)}")
    elif op == "release":
        text = "Released." if gate.release(session, "released") else "You weren't holding Fusion."
    elif op == "forget":
        gate.forget(session)
    elif op == "pause":
        gate.set_pause(auto=False)
        text = gate.status()
    elif op == "resume":
        gate.resume("Cal")
        text = gate.status()
    elif op == "protect":
        gate.protect(body["document"], body.get("reason", ""), bool(body.get("clear")))
        text = gate.status(session)
    elif op == "share":
        gate.cfg.share = bool(body.get("on"))
        log(f"sharing between documents {'on' if gate.cfg.share else 'off'}")
        gate._poke()
        text = f"Sharing between documents is {'on' if gate.cfg.share else 'off'}.\n\n{gate.status(session)}"
    elif op == "status":
        text = gate.status(session)
    else:
        ok, text = False, f"unknown op {op}"
    return web.json_response({"ok": ok, "text": text})


def make_app(gate: Gate):
    from aiohttp import web

    global GATE
    GATE = GATE or web.AppKey("gate", Gate)
    app = web.Application()
    app[GATE] = gate
    app.router.add_post("/", handle)
    return app


async def ticker(gate: Gate) -> None:
    while True:
        await asyncio.sleep(1)
        gate.tick()


async def watch_fusion(gate: Gate, every: float = 3) -> None:
    """A refused connection means Fusion is gone. A slow call is not a hang: rebuilds are slow."""
    url = urlsplit(FUSION)
    misses = 0
    while True:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(url.hostname, url.port), 3)
            writer.close()
            misses = 0
            gate.set_down(False)
        except (OSError, TimeoutError):
            misses += 1
            if misses >= 2:
                gate.set_down(True)
        await asyncio.sleep(every)


async def _output(*cmd: str) -> str:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return out.decode(errors="replace")


async def watch_cal(gate: Gate, every: float = 2) -> None:
    """Keyboard and mouse input reaching Fusion can only be Cal's: agents don't make any."""
    busy_ticks = 0
    while True:
        await asyncio.sleep(every)
        m = re.search(r'"HIDIdleTime" = (\d+)', await _output("ioreg", "-c", "IOHIDSystem", "-d", "4"))
        if not m:
            continue
        touching = int(m.group(1)) / 1e9 < every and "Fusion" in await _output("lsappinfo", "info", "-only", "name", "front")
        now = time.monotonic()
        if touching:
            gate.last_cal_input = now
        if gate.pause:
            limit = gate.cfg.auto_resume if gate.pause.auto else gate.cfg.manual_resume
            if now - gate.last_cal_input >= limit:
                gate.resume(f"Cal hasn't touched Fusion for {ago(limit)}")
                gate.notify("Agents are using Fusion again.")
        elif gate.cfg.auto_pause:
            busy_ticks = busy_ticks + 1 if touching else 0
            if busy_ticks >= 2:
                gate.set_pause(auto=True)
                gate.notify(f"Agents paused while you use Fusion. They resume after "
                            f"{ago(gate.cfg.auto_resume)} without input, or run: fusion_gate.py resume")


def mac_notify(text: str) -> None:
    script = f"display notification {json.dumps(text)} with title \"Fusion gate\""
    subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def serve() -> None:
    from aiohttp import web

    fusion = Fusion(FUSION)
    gate = Gate(Config(), notify=mac_notify, probe=fusion.wait_idle, state=STATE_DIR / "state.json", guard=fusion.guard)
    runner = web.AppRunner(make_app(gate))
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", PORT).start()
    except OSError:
        log(f"port {PORT} is taken: another fusion-gate is already running")
        return
    log(f"fusion-gate on 127.0.0.1:{PORT}, watching {FUSION}")
    # Fusion may still be running a call from before this start: hold everyone until it answers.
    gate.calls["(unknown call from before the gate started)"] = ("", time.monotonic())
    asyncio.ensure_future(gate._clear_stale(time.monotonic() + 1))
    await asyncio.gather(ticker(gate), watch_fusion(gate), watch_cal(gate))


# --- the hook and the commands (standard library only) ----------------------------------


def _post(payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _start_service() -> None:
    uv = shutil.which("uv") or next((p for p in ("/opt/homebrew/bin/uv", str(Path.home() / ".local/bin/uv"))
                                     if os.path.exists(p)), None)
    if not uv:
        raise OSError("uv isn't installed, so the fusion-gate service can't start")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as out:
        subprocess.Popen([uv, "run", "--quiet", "--script", SCRIPT, "serve"], stdin=subprocess.DEVNULL,
                         stdout=out, stderr=out, start_new_session=True)


def call(payload: dict, timeout: float) -> dict:
    """Ask the service, starting it first if it isn't running. `timeout` covers the whole wait."""
    deadline = time.monotonic() + timeout
    try:
        return _post(payload, timeout)
    except ConnectionResetError:
        pass  # the service stopped mid-request, e.g. restarting for an update: ask the next one
    except urllib.error.URLError as e:
        if not isinstance(e.reason, ConnectionRefusedError):
            raise
    _start_service()
    start_by = min(deadline, time.monotonic() + 30)  # the first start downloads aiohttp
    while True:
        try:
            return _post(payload, max(1.0, deadline - time.monotonic()))
        except (ConnectionResetError, urllib.error.URLError) as e:
            refused = isinstance(e, ConnectionResetError) or isinstance(e.reason, ConnectionRefusedError)
            if not refused or time.monotonic() > start_by:
                raise
            time.sleep(0.3)


# Under Claude Code's 90 s hook timeout, which would let the call through, and over the service's own limits.
ADMIT_TIMEOUT = Config.holder_wait + Config.switch_wait + 10


def deny(text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": text}}


GATE_COMMAND = re.compile(r"""fusion_gate\.py['"]?\s+(?:acquire|release)\b""")


def subagent_command(event: dict) -> dict | None:
    """A subagent's acquire or release must say which subagent it is: Bash gives it no way to tell."""
    agent = event.get("agent_id")
    command = str((event.get("tool_input") or {}).get("command") or "")
    if not agent or not GATE_COMMAND.search(command) or f"FUSION_GATE_AGENT={agent}" in command:
        return None
    return deny(f"fusion-gate: you are a subagent, and the gate keeps your lease apart from your parent's "
                f"and your siblings'. This command was {NOT_RUN}. Send it again with "
                f"FUSION_GATE_AGENT={agent} in front, and use that prefix on every acquire and release: "
                f"{gate_command(holder_key('s', agent), '...')}")


def hook(event: dict) -> dict | None:
    """Turn a hook event into the JSON the hook prints, or None to let the call run as normal."""
    name = event.get("hook_event_name")
    session = holder_key(event.get("session_id", ""), event.get("agent_id"))
    where = Path(event.get("cwd") or "").name
    pid = int(os.environ["CLAUDE_PID"]) if os.environ.get("CLAUDE_PID", "").isdigit() else None
    if name == "PreToolUse" and event.get("tool_name") == "Bash":
        return subagent_command(event)
    if name == "PreToolUse":
        tool, args = event.get("tool_name", ""), event.get("tool_input") or {}
        r = call({"op": "admit", "session": session, "where": where, "kind": classify(tool, args), "pid": pid,
                  "call": event.get("tool_use_id"), "tool": tool, "input": args}, ADMIT_TIMEOUT)
        return None if r["ok"] else deny(r["text"])
    if name in ("PostToolUse", "PostToolUseFailure"):
        r = call({"op": "done", "session": session, "call": event.get("tool_use_id")}, 10)
        if r["text"] and name == "PostToolUse":
            return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": r["text"]}}
        return None
    if name in ("SessionEnd", "SubagentStop"):
        call({"op": "forget", "session": session}, 5)
    return None


def _stateless(event: dict) -> bool:
    """Whether the call never looks at a design; False when that can't be told."""
    try:
        return classify(event.get("tool_name", ""), event.get("tool_input") or {}) == "stateless"
    except Exception:
        return False


def run_hook(stdin: str) -> dict | None:
    """The hook, failing closed: a Fusion call the gate couldn't check is refused, apart from
    the stateless ones that never look at a design."""
    try:
        event = json.loads(stdin)
    except ValueError:
        return deny(f"fusion-gate couldn't read the hook's event, so this call was {NOT_RUN}. Tell Cal.")
    try:
        return hook(event)
    except Exception as e:
        print(f"fusion-gate: {type(e).__name__}: {e}", file=sys.stderr)
        if event.get("hook_event_name") not in ("PreToolUse", None) or event.get("tool_name") == "Bash" \
                or _stateless(event):
            return None
        return deny(f"fusion-gate couldn't check this call ({type(e).__name__}: {e}), so it was {NOT_RUN}. "
                    f"Check the gate with `python3 {SCRIPT} status`; its log is {LOG}. Tell Cal if it "
                    f"stays broken: don't work around it.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hook")
    sub.add_parser("serve")
    acquire = sub.add_parser("acquire")
    acquire.add_argument("reason")
    acquire.add_argument("-m", "--minutes", type=float)
    acquire.add_argument("-d", "--documents", default="")
    protect = sub.add_parser("protect")
    protect.add_argument("document")
    protect.add_argument("reason", nargs="?", default="")
    protect.add_argument("--clear", action="store_true")
    share = sub.add_parser("share", help="let agents in different documents take turns call by call")
    share.add_argument("state", choices=["on", "off"])
    release = sub.add_parser("release", help="let go of Fusion, or free a holder whose session is gone")
    freed = release.add_mutually_exclusive_group()
    freed.add_argument("--session", metavar="ID", help="free this holder (its id from status)")
    freed.add_argument("--stale", action="store_true", help="free every holder status calls stale")
    for name in ("status", "pause", "resume"):
        sub.add_parser(name)
    args = parser.parse_args()

    if args.command == "serve":
        asyncio.run(serve())
        return
    if args.command == "hook":
        out = run_hook(sys.stdin.read())
        if out:
            print(json.dumps(out))
        return
    session = holder_key(os.environ.get("CLAUDE_CODE_SESSION_ID", "cal"), os.environ.get("FUSION_GATE_AGENT"))
    payload = {"op": args.command, "session": session, "where": Path.cwd().name}
    if os.environ.get("CLAUDE_PID", "").isdigit():
        payload["pid"] = int(os.environ["CLAUDE_PID"])
    if args.command == "release":
        payload |= {"target": args.session, "stale": args.stale}
    if args.command == "acquire":
        payload |= {"reason": args.reason, "minutes": args.minutes, "documents": args.documents}
    if args.command == "protect":
        payload |= {"document": args.document, "reason": args.reason, "clear": args.clear}
    if args.command == "share":
        payload["on"] = args.state == "on"
    print(call(payload, Config.acquire_wait + 15)["text"])


if __name__ == "__main__":
    main()
