#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.9"]
# ///
"""fusion-gate: agents take turns with Fusion.

Fusion runs every MCP call on its main thread, and two sessions' calls at once can swap each
other's output. A hook on every Fusion tool asks this file's lock service for a turn. The
service lends the design to one session at a time and lets one call at a time through.

    fusion_gate.py hook                              the plugin's hook (reads the event on stdin)
    fusion_gate.py serve                             the lock service; the hook starts it
    fusion_gate.py acquire REASON [-m MIN] [-d DOCS] wait for a turn and hold Fusion
    fusion_gate.py release | status | pause | resume
    fusion_gate.py protect DOCUMENT [REASON] [--clear]

The hook and the commands need only the standard library; the service needs aiohttp, so it
runs under `uv run --script`.
"""

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


@dataclasses.dataclass
class Config:
    wait_block: float = 30  # a Fusion call waits this long for its turn, then is refused
    acquire_wait: float = 100  # under the Bash tool's default 2 min limit
    idle_read: float = 30  # a lease ends after this long without a call
    idle_write: float = 300
    idle_explicit: float = 600  # once the holder has said what it is doing
    ageing: float = 600  # a waiter this old goes to the front
    place_kept: float = 900  # a session that stopped waiting keeps its place this long
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
    told: bool = False
    overrun_told: bool = False


@dataclasses.dataclass
class Pause:
    since: float
    auto: bool


class Gate:
    def __init__(self, config: Config | None = None, notify=lambda text: None, probe=None):
        self.cfg = config or Config()
        self.notify = notify
        self.probe = probe  # returns once Fusion has finished whatever it is running
        self.lease: Lease | None = None
        self.pause: Pause | None = None
        self.down = False
        self.calls: dict[str, tuple[str, float]] = {}  # call in Fusion -> (session, started)
        self.waiting: dict[str, tuple[int, float]] = {}  # session -> (rank, queued since)
        self._refs: dict[str, int] = {}  # concurrent waits per session
        self.places: dict[str, tuple[float, float]] = {}  # session -> (queued since, left at)
        self.intent: dict[str, dict] = {}  # session -> what acquire said
        self.where: dict[str, str] = {}  # session -> the project it runs in
        self.protected: dict[str, str] = {}  # document -> why
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

    def queue(self) -> list[str]:
        now = time.monotonic()
        return [s for s, _ in sorted(self.waiting.items(), key=lambda i: self._key(i, now))]

    def _rank(self, session: str, kind: str) -> int:
        if kind == "read":
            return 0
        minutes = self.intent.get(session, {}).get("minutes")
        return 2 if minutes and minutes > 10 else 1

    def _start(self, call: str | None, session: str) -> None:
        if call:
            self.calls[call] = (session, time.monotonic())

    async def admit(self, session: str, kind: str, timeout: float, call: str | None = None,
                    through_down: bool = False):
        """Wait for `session`'s turn. With `call`, also wait for Fusion to be idle and record the
        call as running. Returns None once through, otherwise why not: 'down', 'paused', 'busy'."""
        if kind == "stateless":
            return await self._wait(timeout, through_down, lambda: self._slot(call, session))
        now = time.monotonic()
        rank = self._rank(session, kind)
        if session in self.waiting:
            rank, since = min(rank, self.waiting[session][0]), self.waiting[session][1]
        else:
            since, left = self.places.pop(session, (now, now))
            if now - left >= self.cfg.place_kept:
                since = now
        self.waiting[session] = (rank, since)
        self._refs[session] = self._refs.get(session, 0) + 1

        def turn():
            if self.pause or self.down or (call and self.calls):
                return False
            if self.lease is None and self.queue()[0] == session:
                self._grant(session)
            if not (self.lease and self.lease.session == session):
                return False
            idle = self.cfg.idle_read if kind == "read" else self.cfg.idle_write
            self.lease.idle = max(self.lease.idle, idle)
            self._start(call, session)
            return True

        try:
            return await self._wait(timeout, through_down, turn)
        finally:
            self._refs[session] -= 1
            if not self._refs[session]:
                del self._refs[session]
                _, since = self.waiting.pop(session)
                if not (self.lease and self.lease.session == session):
                    self.places[session] = (since, time.monotonic())
                self._poke()

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
        lease = self.lease
        self._poke()
        if not (lease and lease.session == session):
            return ""
        lease.last_call = time.monotonic()
        if lease.told:
            return ""
        lease.told = True
        return self.welcome(lease)

    def welcome(self, lease: Lease) -> str:
        return (f"fusion-gate: you hold Fusion until you run `python3 {SCRIPT} release` or make no Fusion "
                f"call for {ago(lease.idle)}. The active document may be another agent's: activate yours "
                f"and check app.activeDocument.name before changing anything.") + self._protected_line()

    def _grant(self, session: str) -> None:
        now = time.monotonic()
        self.lease = Lease(session, idle=self.cfg.idle_read, since=now, last_call=now)
        self.places.pop(session, None)
        log(f"Fusion → {self.describe(session)}")

    def release(self, session: str, why: str) -> bool:
        if not (self.lease and self.lease.session == session):
            return False
        log(f"{self.describe(session)} let go of Fusion: {why}")
        self.lease = None
        self._poke()
        return True

    def forget(self, session: str) -> None:
        self.release(session, "its session ended")
        self.places.pop(session, None)
        self.intent.pop(session, None)

    def busy(self, session: str) -> bool:
        return any(s == session for s, _ in self.calls.values())

    def tick(self) -> None:
        now = time.monotonic()
        lease = self.lease
        if lease and not self.busy(lease.session) and not self.pause and now - lease.last_call > lease.idle:
            self.release(lease.session, f"no call for {ago(now - lease.last_call)}")
        if (lease and self.cfg.notify_overrun and lease.minutes and not lease.overrun_told
                and now - lease.since > 1.5 * lease.minutes * 60):
            lease.overrun_told = True
            self.notify(f"{self.describe(lease.session)} has held Fusion for {ago(now - lease.since)}, "
                        f"over its {lease.minutes:g} min estimate.")
        if self.probe and not self._probing and any(now - t > self.cfg.stale_call for _, t in self.calls.values()):
            self._probing = True
            asyncio.ensure_future(self._clear_stale(now))
        for session, (_, left) in list(self.places.items()):
            if now - left >= self.cfg.place_kept:
                del self.places[session]

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
            self.lease = None
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
        if self.lease:
            self.lease.last_call = time.monotonic()
        log(f"resumed: {why}")
        self._poke()

    async def acquire(self, session: str, reason: str, minutes: float | None, documents: str) -> str:
        self.intent[session] = {"reason": reason, "minutes": minutes, "documents": documents}
        why = await self.admit(session, "write", self.cfg.acquire_wait, through_down=True)
        if why:
            place = self.queue().index(session) + 1 if session in self.waiting else None
            return (f"Not your turn yet{f' (you are #{place})' if place else ''}; your place is kept. "
                    f"Run the same command again.\n\n{self.status(session)}")
        lease = self.lease
        lease.reason, lease.minutes, lease.documents = reason, minutes, documents
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
        name = f"an agent in {where}" if where else f"agent {session[:4]}"
        return f"{name} ({reason})" if reason else name

    def _protected_line(self) -> str:
        if not self.protected:
            return ""
        return "\n\nNever save or close: " + "; ".join(f"{d} ({why})" for d, why in self.protected.items())

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
        lease = self.lease
        if lease:
            parts = [f"Held by {self.describe(lease.session, viewer)} for {ago(now - lease.since)}"]
            if lease.documents:
                parts.append(f"documents: {lease.documents}")
            if lease.minutes:
                parts.append(f"expects {lease.minutes:g} min")
            parts.append("a call is running" if self.busy(lease.session) else f"last call {ago(now - lease.last_call)} ago")
            lines.append(", ".join(parts) + ".")
        elif not self.down and not self.pause:
            lines.append("Fusion is free.")
        if self.waiting:
            queue = [self.describe(s, viewer) for s in self.queue()]
            lines.append("Waiting: " + ", ".join(f"{i}. {q}" for i, q in enumerate(queue, 1)))
        return "\n".join(lines) + self._protected_line()


def refusal(gate: Gate, why: str, session: str) -> str:
    head = {
        "busy": "Fusion is in use by another agent",
        "paused": "Cal has taken over Fusion",
        "down": "Fusion isn't reachable; it may have crashed or been closed",
    }[why]
    if why == "down":
        tail = "Don't retry in a loop. Tell Cal, or wait for Fusion with the command below."
    else:
        tail = "Don't message other agents about it. Wait your turn with the command below, or do other work and try later."
    command = (f"python3 {SCRIPT} acquire \"<what you are doing>\" -m <minutes>   "
               f"(waits up to {ago(gate.cfg.acquire_wait)} and keeps your place; run it again if still queued)")
    return f"{head}, so your call was NOT run.\n\n{gate.status(session)}\n\n{tail}\n{command}"


# --- the service ------------------------------------------------------------------------


class Fusion:
    """Fusion's own MCP server, used only to find out when it has finished a call."""

    def __init__(self, url: str):
        self.url = url
        self.sid: str | None = None

    async def _post(self, http, msg: dict, sid: str | None = None):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if sid:
            headers["Mcp-Session-Id"] = sid
        async with http.post(self.url, json=msg, headers=headers) as r:
            return r.headers.get("Mcp-Session-Id"), await r.text()

    async def wait_idle(self) -> None:
        import aiohttp

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=5)) as http:
            hello = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fusion-gate", "version": "1"}}
            sid, _ = await self._post(http, {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": hello})
            await self._post(http, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
            # Any tool call queues behind whatever Fusion is running; this one is cheap.
            lookup = {"queryType": "apiDocumentation", "searchPattern": "^Application$", "apiCategory": "class"}
            await self._post(http, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": "fusion_mcp_read", "arguments": lookup}}, sid)


GATE = None  # aiohttp's AppKey, made with the app so the hook never imports aiohttp


async def handle(request):
    from aiohttp import web

    gate: Gate = request.app[GATE]
    body = await request.json()
    op, session = body.get("op"), body.get("session") or "unknown"
    if body.get("where"):
        gate.where.setdefault(session, body["where"])
    ok, text = True, ""
    if op == "admit":
        why = await gate.admit(session, body["kind"], gate.cfg.wait_block, call=body.get("call"))
        ok, text = why is None, refusal(gate, why, session) if why else ""
    elif op == "done":
        text = gate.done(body.get("call", ""))
    elif op == "acquire":
        text = await gate.acquire(session, body.get("reason", ""), body.get("minutes"), body.get("documents", ""))
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
        if body.get("clear"):
            gate.protected.pop(body["document"], None)
        else:
            gate.protected[body["document"]] = body.get("reason") or "protected"
        text = gate.status()
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

    gate = Gate(Config(), notify=mac_notify, probe=Fusion(FUSION).wait_idle)
    runner = web.AppRunner(make_app(gate))
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", PORT).start()
    except OSError:
        log(f"port {PORT} is taken: another fusion-gate is already running")
        return
    log(f"fusion-gate on 127.0.0.1:{PORT}, watching {FUSION}")
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
    """Ask the service, starting it first if it isn't running."""
    try:
        return _post(payload, timeout)
    except urllib.error.URLError as e:
        if not isinstance(e.reason, ConnectionRefusedError):
            raise
    _start_service()
    deadline = time.monotonic() + 30  # the first start downloads aiohttp
    while True:
        try:
            return _post(payload, timeout)
        except urllib.error.URLError as e:
            if not isinstance(e.reason, ConnectionRefusedError) or time.monotonic() > deadline:
                raise
            time.sleep(0.3)


def hook(event: dict) -> dict | None:
    """Turn a hook event into the JSON the hook prints, or None to let the call run as normal."""
    name, session = event.get("hook_event_name"), event.get("session_id", "")
    where = Path(event.get("cwd") or "").name
    if name == "PreToolUse":
        kind = classify(event.get("tool_name", ""), event.get("tool_input") or {})
        r = call({"op": "admit", "session": session, "where": where, "kind": kind,
                  "call": event.get("tool_use_id")}, Config.wait_block + 15)
        if r["ok"]:
            return None
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": r["text"]}}
    if name in ("PostToolUse", "PostToolUseFailure"):
        r = call({"op": "done", "session": session, "call": event.get("tool_use_id")}, 10)
        if r["text"] and name == "PostToolUse":
            return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": r["text"]}}
        return None
    if name == "SessionEnd":
        call({"op": "forget", "session": session}, 5)
    return None


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
    for name in ("release", "status", "pause", "resume"):
        sub.add_parser(name)
    args = parser.parse_args()

    if args.command == "serve":
        asyncio.run(serve())
        return
    if args.command == "hook":
        try:
            out = hook(json.load(sys.stdin))
        except OSError as e:  # never let a broken gate stop Fusion work
            print(f"fusion-gate unavailable, letting the call through: {e}", file=sys.stderr)
            return
        if out:
            print(json.dumps(out))
        return
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "cal")
    payload = {"op": args.command, "session": session, "where": Path.cwd().name}
    if args.command == "acquire":
        payload |= {"reason": args.reason, "minutes": args.minutes, "documents": args.documents}
    if args.command == "protect":
        payload |= {"document": args.document, "reason": args.reason, "clear": args.clear}
    print(call(payload, Config.acquire_wait + 15)["text"])


if __name__ == "__main__":
    main()
