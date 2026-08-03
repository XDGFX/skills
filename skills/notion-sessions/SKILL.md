---
name: notion-sessions
description: "Cal's persistent work journal lives in Notion; this skill is the ONLY correct way to read or write it. Always use it — never answer from memory or improvise a Notion call — when the user: asks what was decided, discussed, or worked on before ('what did we decide about X?', 'find that session about Y', 'it's written up somewhere'); says a decision is settled and should be recorded; asks to log, save, or write up this session's work, findings, or analysis so they can read it later (session write-ups always go to Notion, named or not); signals wrap-up of substantial work ('that's merged', 'before I head off, save this'); or mentions their Notion workspace at all. Any request to remember, retrieve, or durably record project work = this skill, immediately, before any other tool. Do not use for coding tasks that merely involve the words session/notes/notion (Notion-style UIs, MCP server setup, database tables, scratch files, app logging). Trigger: /log-session."
---

# notion-sessions

Cal's Notion workspace is the durable memory across coding sessions: project pages under a Projects parent, and a **Sessions database** of dated work notes that later sessions read back as reference material. This skill replaces the old "Agent Instructions" page in Notion (now archived) — everything an agent needs is here.

**First step, always:** read `references/workspace-map.md`. It contains the Sessions database ID and schema, the closed Tags/Project vocabularies, and an index of canonical pages. Using it avoids the three failure modes that recur in transcripts: re-fetching the database schema every session, guessing tag values that fail validation, and re-searching for pages that are already known.

Write all Notion content in British English. Notes are internal working documents — practical and information-dense, not polished prose.

## Recalling prior work

Cal usually describes pages from memory ("the session where we compared with an existing business game") rather than linking them.

1. Check the canonical pages index in the workspace map first. If the description matches an indexed page, fetch it directly by ID — skip searching.
2. Otherwise `notion-search`. If the first query misses, reformulate once with different key nouns before widening.
3. When several similarly-titled versions surface (v1 / v2 / Final), prefer the one marked Final or most recent, and tell Cal which one you used.
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
   - **Tags** — 1–2 values from the closed list in the map. Never invent a value; if nothing fits, pick the nearest and tell Cal it was approximate (see Self-maintenance).

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

- **Unindexed page resolved by search** → append/increment a line in the ledger (title, ID, project, hit count, date). At **2 hits**, after finishing the task, ask Cal once: "Second session that's needed *X* — add it to the skill's workspace map?" On yes, move it into the map's canonical index; on no, mark it `declined` and never re-ask.
- **New project slug or database property change** (e.g. a Tags validation failure, a Project option that's appeared or vanished) → prompt immediately, same pattern. A validation failure means the map is stale: fetch the schema once, complete the write, then propose the map update.
- Keep ledger entries one line each; prune `added`/`declined` rows on sight if the ledger exceeds ~20 lines.

Always finish the user's actual task before raising a maintenance prompt, and never ask more than once per session.
