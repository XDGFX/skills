# Claude Skills — xdgfx

A collection of Claude Code skills.

## Install (Claude Code)

```
/plugin install github:xdgfx/skills
```

## Install (npx skills)

```bash
npx skills add xdgfx/skills@wireviz
```

## Skills

### wireviz

Generate, modify, and extend WireViz YAML files for documenting electrical cables and wiring harnesses.

### tracker-sweep

Audit a GitHub issue tracker for staleness, wrong or missing blockers, duplicates, conflicting
tickets, orphaned epic children and ordering mistakes — then propose fixes and apply only the
approved ones. Every write is re-validated against the live tracker first, and the whole plan is
refused if any single action has gone stale. Designed to be re-run every few days on a large
tracker: the second sweep only deep-reads what moved. Trigger: `/tracker-sweep`.
