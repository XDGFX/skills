---
name: wireviz
description: >
  Generate, modify, and extend WireViz YAML files for documenting electrical cables and wiring harnesses.
  Use this skill whenever the user mentions WireViz, cable harnesses, wiring diagrams, connectors, wiring YAML,
  or wants to add/change/extend anything in a .yml file that describes electrical wiring. Also trigger for
  requests like "add a connector", "extend the harness", "add a sensor to the wiring", "change the wire colour",
  "update the pinout", "add a cable run", or any task involving electrical wiring documentation.
  Even if the user doesn't say "WireViz" explicitly — if they're editing a wiring YAML file or describing
  cable/connector work, use this skill.
---

# WireViz Skill

WireViz converts YAML files into wiring harness diagrams. The YAML must be exactly right — small errors cause
cryptic failures. Your job is to produce valid, idiomatic WireViz YAML by reading the spec carefully and
inferring as much as possible from existing files before asking the user questions.

**Full syntax reference:** `references/wireviz-syntax.md` — read this before making any changes.

---

## Core Workflow

### 1. Always read the existing file first

Before doing anything else, read the full `.yml` file the user is working on. Also check whether there is a
`shared.yml` (or similar prepended file) that defines reusable templates and anchors — this is common in
larger projects and will define connectors, cables, and YAML anchors that you can reuse.

Look for:
- Naming conventions (e.g. `SYSTEM_NAME_CONN`, `SYSTEM_NAME_CABLE`, or simple `X1`/`W1`)
- Cable gauge and colour patterns in use (so new elements match)
- YAML anchor templates (e.g. `&deutsch_8pin`, `&twin_core_cable`) that you should reuse
- Background colours used for specific component types
- Whether `wirelabels` are used (functional names like `SIGNAL_GND`) or just numeric wire references

### 2. Infer what you can

Before asking the user anything, derive as much as possible from the existing YAML:
- **Wire gauge** — match the gauge of whatever cable is connecting into the same connector
- **Wire colours** — if the system uses a colour-coded bundle, continue the same colour pattern; if not, use
  logical conventions (RD=power, BK/WH=ground, others for signals)
- **Connector type** — if it's a multi-wire interconnect, check if there's a standard template already defined
  (e.g. `&deutsch_8pin`) and reuse it
- **Pin labels** — if the user says "add a temperature sensor", and you can see that other sensors use a
  `SIGNAL_GND` pin alongside the signal, carry that pattern forward
- **bgcolor** — match the colour scheme used for similar components

### 3. Ask targeted clarifying questions

Only ask what you genuinely cannot infer. Group questions together and ask them all at once — don't drip-feed
one question at a time. Good things to clarify:

- **Which fuse/circuit** — "off the fuse panel" is ambiguous; ask which channel, or propose the next unused
  one and ask the user to confirm
- **Wire length** — almost never inferable; always ask unless the user states it
- **Pin count and function** of a new device (if not obvious from context)
- **Whether a new connector is a mate pair** of an existing one (same type, opposite subtype)
- **Whether the user wants to use existing cable stock** — they may have a reel of a specific gauge already;
  if so, note any electrical concerns (see section below) but respect their choice
- **Whether a sensor/device comes with pre-made wires** or needs terminating
- **Whether to add a fuse or relay** in the new run

Frame questions specifically: not "what connector do you want?" but "should I use the Deutsch DT 8-pin template
already in your file, or is this a different connector type?"

**When something is genuinely ambiguous, prefer asking over assuming.** A wrong guess wastes the user's
time and produces a diagram that doesn't match their physical installation.

### 4. Output clean, valid YAML

When outputting changes:
- Show the **full updated section**, not a diff — users can paste it directly
- If only adding new components, show the new blocks clearly labelled with a comment like
  `# --- NEW: Shower temperature extension ---`
- Reuse existing YAML anchors where appropriate (`<<: *deutsch_8pin`)
- Maintain the same indentation style (2 spaces is standard)
- Use `wirelabels` if the file already uses them; don't mix numeric and label references in the same file

---

## Extending an Existing Diagram

This is the most common task: the user has a diagram that ends at a connector, and wants to continue it further.

**Steps:**
1. Identify the connector being extended — read its `pinlabels` to understand what signals are present
2. Check what cable currently terminates at that connector (what gauge, colours, and labels it uses)
3. Determine what new component(s) are being added (device, intermediate connector, sensor, etc.)
4. Build the chain: new cable → new connector(s) → device
5. Write new `connectors:` and `cables:` entries, then add `connections:` entries to wire them up

**Multi-stage extensions** (cable → fuse → cable → connector → device) are expressed as a single connection
entry with multiple elements in sequence. See the syntax reference for how intermediate components work.

**Reusing a connector for two separate runs** (e.g. a harness connector that splits into A-side and B-side)
uses the suffix notation: `CONN.CONN_B` creates a second instance of `CONN`.

---

## Electrical Validation

WireViz is a documentation tool — it won't stop you writing a physically wrong diagram. You should catch
electrical problems before outputting YAML and flag them to the user.

### Cable gauge vs current capacity

Approximate continuous current ratings for common gauges (at ~70°C, in a bundle):

| mm²   | AWG (approx) | Max continuous current |
|-------|--------------|------------------------|
| 0.25  | 24 AWG       | ~2 A (signal only)     |
| 0.5   | 20 AWG       | ~5 A                   |
| 0.75  | 18 AWG       | ~7.5 A                 |
| 1.0   | 16–17 AWG    | ~10 A                  |
| 1.5   | 16 AWG       | ~16 A                  |
| 2.5   | 14 AWG       | ~25 A                  |
| 4.0   | 12 AWG       | ~32 A                  |
| 6.0   | 10 AWG       | ~40 A                  |
| 10.0  | 8 AWG        | ~55 A                  |
| 16.0  | 6 AWG        | ~73 A                  |
| 25.0  | 4 AWG        | ~95 A                  |

**If the user specifies (or you infer) a gauge that seems undersized for the expected load**, say so clearly
before outputting YAML. For example: "A 12V→5V USB supply drawing up to 3 A needs at least 0.5 mm² cable —
I've used 1 mm² to give a safe margin. If you want to use your existing 0.25 mm² stock, that's fine for
signal wiring but I'd recommend upgrading for a power supply."

**If the user explicitly tells you their existing cable stock and it's marginal**, document it in a `notes`
field on the cable in the YAML, then note the concern but proceed. Don't refuse — the user may have
constraints you don't know about.

### Fuse sizing

If adding a new circuit off a fuse panel, check:
- The fuse should protect the **cable**, not just the device
- Fuse rating should be ≤ the cable's current capacity
- Typical rule: fuse at ~80% of cable capacity, or just above the expected load

If the user hasn't specified a fuse and you're adding a new circuit off a supply, ask whether they want one
included.

---

## Creating a New Diagram

If writing from scratch, ask the user for:
1. What devices need connecting (name, pin count, pin functions)
2. Cable length(s)
3. Wire gauge (if they know it; otherwise suggest based on typical current for the application)
4. Whether this is a one-off or part of a larger system with shared templates

Start with the simplest working structure and add complexity only as needed.

---

## Validation Checklist

Before presenting output, mentally verify:
- Every connector has at least one of: `pincount`, `pins`, or `pinlabels`
- Every pin referenced in `connections:` actually exists on that connector
- Every wire index referenced in `connections:` is within the cable's `wirecount`
- Wire counts in a connection step all match (if connecting 4 pins, use 4 wires)
- Cable colours array length matches `wirecount` (if explicit colours are given)
- `wirelabels` array length matches `wirecount`
- YAML anchors are defined before they are used
- Indentation is consistent (2 spaces)
- The `connections:` block uses the right list structure — each connection is a list item (dash), and the
  elements within it are also a list

### Run WireViz to confirm

After producing the YAML, **always try to run WireViz on it** to catch syntax errors that mental review
misses. Ask the user how WireViz is installed in their project (global, virtualenv, etc.) and use that.

```bash
# If the file uses a prepend/shared file for templates:
wireviz --prepend <shared.yml> <output_file.yml>

# Standalone file:
wireviz <output_file.yml>
```

If adding to an existing diagram, write the new additions to a temp file and use the original as prepend — this tests just the new parts without duplicating the whole diagram.

If WireViz errors, read the error message carefully — it usually names the exact connector or pin that's
wrong. Fix the YAML and run again before presenting to the user. Don't present output you haven't validated.

---

## Common Mistakes to Avoid

- **Missing pincount**: WireViz will error if a connector has neither `pincount` nor `pinlabels`. If you're
  using `pinlabels`, you don't also need `pincount` — it's derived automatically.
- **Off-by-one wire references**: Wires are 1-indexed. A 3-wire cable has wires 1, 2, 3.
- **Mismatched list lengths in connections**: `[X1: [1,2,3], W1: [1,2], X2: [1,2,3]]` will error.
- **Using a label reference that doesn't match exactly**: Pin label references are case-sensitive.
- **Forgetting the shield**: Shielded cables have a wire `s` in addition to numbered wires. Connecting to
  `s` requires an explicit connection entry.
- **Reusing a component in connections without creating an instance**: If you connect to a component twice
  in different runs, WireViz may conflict. Use the `.SUFFIX` notation to create separate instances.
- **Connector directly connected to connector (no cable between them)**: WireViz requires a cable between
  any two connectors. `connector → connector` is invalid and will error with "Expected cable/arrow, but X is
  connector". Small passive components like pull-up resistors that sit between two pins cannot be modelled
  as connectors connected to each other — put them in a `notes` field instead, or add a short cable stub.
