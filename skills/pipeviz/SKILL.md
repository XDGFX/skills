---
name: pipeviz
description: >
    Generate, modify, and extend pipeviz YAML files describing plumbing systems.
    Use this skill whenever the user mentions plumbing diagrams, pipe runs, tanks, pumps, valves,
    fittings, service ratings, or wants to add/change/extend anything in a .yml file that describes
    plumbing. Also trigger for requests like "add a valve", "connect the tank to the pump",
    "add a filter to the system", "extend the fresh water supply", "update the pipe routing",
    "add a tee fitting", or any task involving plumbing system documentation.
    Even if the user doesn't say "pipeviz" explicitly — if they're editing a YAML file
    that describes pipe/component work, use this skill.
---

# Pipeviz Skill

Pipeviz generates plumbing system diagrams from YAML files. The YAML format is similar to WireViz
but for pipes and plumbing components. Small errors cause validation failures — read the spec
carefully and infer as much as possible from existing files before asking questions.

**Full syntax reference:** `references/syntax.md` — read this before making any changes.

---

## Core Workflow

### 1. Always read the existing file(s) first

Before doing anything, read the full `.yml` file the user is working on. If the project uses a
prepend file (commonly named `shared.yml`), read that too — it defines reusable component
definitions that can be referenced with `ref:` or `template:`.

Look for:

- Naming conventions (e.g. `CAPS_WITH_UNDERSCORES` for components, `snake_case` for pipes)
- Pipe types already declared under `pipes:` (so new runs match existing ones)
- `service_rating` patterns in use (`potable`, `waste`, `vent`, `hot`) — continue them
- Whether components use `simple: true` (edge attachment only, no port table) or named ports
- Any YAML anchors in the prepend file that can be reused

### 2. Infer what you can

Before asking questions, derive as much as possible from the existing YAML:

- **Pipe type** — reuse existing declared pipe types where possible
- **Service rating** — match the rating of whatever system is being extended
- **Component template** — look at similar components and use the same `template:`
- **Port names** — if extending a component that already exists in the prepend file, its ports are
  already defined
- **`simple: true`** — use it for fittings/adapters that just pass through and don't need a port
  table; use named ports only when flow direction matters for the diagram

### 3. Ask targeted clarifying questions

Only ask what you cannot infer. Ask all questions at once, not one at a time. Good things to ask:

- **Which port** on an ambiguous multi-port component (e.g. which branch of a tee)
- **Pipe size** — often inferrable from context, but ask if unclear
- **Whether a new component should go in the prepend file** (reusable across diagrams) or just
  in the diagram file (one-off)

Frame questions specifically: not "what fitting do you want?" but "should I use the
`tee_25mm_barb` already in shared.yml, or is this a different size tee?"

---

## Extending an Existing Diagram

The most common task: the user has a diagram and wants to extend it.

**Steps:**

1. Read the file — identify where the extension connects and what ports are available
2. Check the prepend file for existing component definitions you can `ref:`
3. Determine what new components/pipes are needed
4. Build the chain in `connections:`, following the token syntax in `SYNTAX.md`
5. If adding a wholly new component type, decide: add it to the prepend file (if reusable) or
   define it inline in the diagram file

---

## Service Ratings and Colour Coding

Always set `service_rating` on pipe types to get automatic colour coding:

- `potable` — cold drinking water
- `hot` — heated water
- `waste` — greywater drain
- `vent` — vent lines

---

## Creating a New Diagram

If writing from scratch, ask the user for:

1. What components need to be in this system (type, key connections)
2. Whether to pull components from a prepend file (preferred) or define inline
3. Flow direction preference (`rankdir: LR` left-to-right is default; `TB` for vertical layout)
4. Which service(s) this diagram covers

Start minimal — get the main flow path working first, then add branches and accessories.

---

## Validation Checklist

Before presenting output, verify mentally:

- Every component has a `label` and either `ports`, `portcount`, or `simple: true`
- Every `ref:` target exists in the prepend file
- Every `template:` target exists in the prepend file (or is a built-in — see `SYNTAX.md`)
- Every port referenced in `connections:` exists on that component (or it uses `simple: true`)
- No pipe-to-pipe adjacency in any chain
- Every chain has at least 2 tokens
- Every pipe type used in `connections:` is declared under `pipes:`

### Run the generator to confirm

After producing YAML, **always try to run the generator** to catch errors. From the project
directory:

```bash
# Without a prepend file
pipeviz path/to/diagram.yml

# With a shared component library
pipeviz --prepend shared.yml path/to/diagram.yml

# Specify output format or directory
pipeviz --prepend shared.yml --format png --output-dir out/ path/to/diagram.yml
```

If the generator errors, read the message — it names the exact component or chain that failed.
Fix and re-run before presenting output. Don't present YAML you haven't validated.

---

## Common Mistakes to Avoid

- **Missing `label`**: Every component must have a label — the generator will error.
- **`ports` and `portcount` mismatch**: If both are given, `portcount` must equal `len(ports)`.
- **`simple: true` with port references in connections**: Simple components attach to the node
  border — you cannot reference a named port like `component:inlet`. Just use `component`.
- **Pipe-to-pipe adjacency**: `[tank, pipe_a, pipe_b, pump]` is invalid. Always put a component
  between two pipe tokens.
- **Unknown `ref:` target**: `ref:` names must match a key in the prepend file's `components:` block.
- **Forgetting to declare pipe types**: Every pipe used in `connections:` must be declared under
  `pipes:` with at least a `label`.
- **Wrong instance syntax**: `component.name` creates a named reusable instance; `component.`
  creates a fresh anonymous instance each time. Use `component.` for tees where each branch
  is a distinct occurrence.
