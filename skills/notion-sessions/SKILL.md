---
name: notion-sessions
description: "Read and write the user's Notion work journal — the Sessions database of dated work notes, and the project pages under Projects. Use it whenever the request reaches that workspace: the user names Notion; asks what was decided, discussed or worked on in an earlier session; asks to log, save or write up this session; or asks to record a decision against a project. Also use it when substantial work concludes with a deliverable or decision worth logging — then ask before writing anything. This is the only correct way to reach Notion: never improvise a Notion call or answer from memory. Not for coding tasks that merely involve the words session, note or database (Notion-style UIs, MCP server setup, scratch files, app logging). Triggers: /notion-sessions, /log-session."
---

# notion-sessions

The user's Notion workspace is the durable memory across coding sessions: project pages under a Projects parent, and a **Sessions database** of dated work notes that later sessions read back as reference material. This skill replaces the old "Agent Instructions" page in Notion (now archived) — everything an agent needs is here.

**First step, always:** read `references/workspace-map.md`. It contains the Sessions database ID and schema, the closed Tags/Project vocabularies, and an index of canonical pages. Using it avoids the three failure modes that recur in transcripts: re-fetching the database schema every session, guessing tag values that fail validation, and re-searching for pages that are already known.

Write all Notion content in British English. Notes are internal working documents — practical and information-dense, not polished prose.

**Reads are free; writes are not.** Recalling prior work needs no permission — fetch and answer. But when this skill was invoked off your own judgement rather than an explicit request to write (a session that has just concluded, a decision that looks worth recording), say what you propose to log and where, and get a yes before creating or appending anything. A session note the user did not ask for is noise in their journal.

## Recalling prior work

The user usually describes pages from memory ("the session where we compared with an existing business game") rather than linking them.

1. Check the canonical pages index in the workspace map first. If the description matches an indexed page, fetch it directly by ID — skip searching.
2. Otherwise `notion-search`. If the first query misses, reformulate once with different key nouns before widening.
3. When several similarly-titled versions surface (v1 / v2 / Final), prefer the one marked Final or most recent, and tell the user which one you used.
4. If a search-resolved page was not in the index, record it in the candidates ledger (see Self-maintenance).

## Writing a session note (/log-session)

**When to log:** sessions that produced a deliverable, analysis, design, or decision get a note. Pure lookups, quick fixes, and read-only investigations do not — don't create noise. Mid-session checkpoint notes (e.g. a plan document written before a long build) are fine and count as that session's note unless substantial further outcomes accrue, in which case write a closing note too.

**Procedure:**
1. Read the workspace map. Do not fetch the Sessions database to re-derive its schema — the map is authoritative (if a write fails validation, see Self-maintenance).
2. Create the page in the Sessions data source with:
   - **Title** — informative and searchable, typically `Topic — subtitle` with an em-dash (e.g. "Competitor Analysis — GoVenture CEO vs strat.bz").
   - **Icon** — a topically apt emoji.
   - **Project** — one of the closed slugs in the map. `general` is the catch-all for non-project work.
   - **Date** — today. `date:Date:is_datetime` must be the integer `0`, not the string `"0"`.
   - **Status** — `Complete` (use `Draft` only if the note is knowingly unfinished).
   - **Tags** — 1–2 values from the closed list in the map. Never invent a value; if nothing fits, pick the nearest and tell the user it was approximate (see Self-maintenance).

**Body structure** — use the sections that apply, omit empty ones:

```markdown
> Scope this session: [one-line framing of what was attempted]

## Outcome
[what was achieved; tables welcome for results/comparisons]

## Decisions taken
[what + why, one bullet each]

## Open questions
## Next
[concrete next steps]

Repo: [repo-name], commits [hashes]   ← include when working in a git repo
```

## Logging decisions to project pages

When a significant design or technical decision is made, append it to the **project page itself** (not just the session note), under its `# Decisions` section (create the section at the bottom if absent):

```markdown
### [Decision title] — YYYY-MM-DD
**Decision:** [what was decided]
**Reason:** [why]
```

This has historically been skipped — the decision ends up only in the session note. Do both: the session note carries the narrative, the project page carries the durable record.

## Self-maintenance (keeping the map current)

The workspace map is useful only while it matches reality, so the skill maintains itself via `references/candidates.md`:

- **Unindexed page resolved by search** → append/increment a line in the ledger (title, ID, project, hit count, date). At **2 hits**, after finishing the task, ask the user once: "Second session that's needed *X* — add it to the skill's workspace map?" On yes, move it into the map's canonical index; on no, mark it `declined` and never re-ask.
- **New project slug or database property change** (e.g. a Tags validation failure, a Project option that's appeared or vanished) → prompt immediately, same pattern. A validation failure means the map is stale: fetch the schema once, complete the write, then propose the map update.
- Keep ledger entries one line each; prune `added`/`declined` rows on sight if the ledger exceeds ~20 lines.

Always finish the user's actual task before raising a maintenance prompt, and never ask more than once per session.
