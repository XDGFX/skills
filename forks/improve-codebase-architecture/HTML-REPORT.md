# HTML Report Format

The report is `report-template.html`, filled in. The template owns the look: keep its `<head>`, stylesheet and Mermaid script whole, and build `<main>` from the components it shows once each. This file says what goes in them.

The diagrams carry the weight. Prose is sparse, plain, and uses the glossary terms (from the `/codebase-design` skill) without ceremony. No paragraphs of explanation: if a diagram needs a paragraph to be understood, redraw the diagram.

## Masthead

- **Eyebrow**: "Architecture review" and the date.
- **Title**: the repo name.
- **Scope**: the paths scanned and why (the user's direction, or the hot spots in the last N commits).
- **Strip and tally**: one segment per recommendation strength, `flex-grow` set to its count; drop a segment whose count is zero.
- **Legend**: keep it as the template has it.

## Top recommendation

One card linking to its candidate by `id`: the candidate's name and one sentence on why it goes first.

## Candidates

Strongest first. Each is one `article.candidate` with its strength as a class (`strong`, `exploring`, `speculative`):

- **Tags**: the strength (`Strong`, `Worth exploring`, `Speculative`) and the dependency category (`In-process`, `Local-substitutable`, `Ports & adapters`, `Mock`).
- **Title**: short, names the deepening (e.g. "Collapse the Order intake pipeline").
- **Files**: each file or module involved, in `<code>`.
- **Before / After**: the centrepiece, side by side. See the diagram kit below.
- **Problem**: one sentence. What hurts.
- **Solution**: one sentence. What changes.
- **Wins**: bullets, six words or fewer each.
- **ADR callout** (only when the candidate contradicts an ADR): one line in `p.adr`, e.g. "Contradicts ADR-0007, but worth reopening: …".

## Diagram kit

Pick the kit that fits the point, and mix them: a report where every diagram looks the same is generic. Before and after may use different kits.

- **Mermaid** (`pre.mermaid`): graph-shaped structure, such as call flow, dependencies, or a sequence diagram for "before: six round-trips; after: one". Colour leaking modules with `classDef leak` and leaking edges with `linkStyle`, as the template does.
- **Boxes** (`.mod`, `.mod.deep`, `.seam`, `.callers`, `.arrow`): when the point is weight. The after of a deepening is usually one `.mod.deep` with the now-internal parts faded inside it, callers above the seam.
- **Cross-section** (`.layers`, `.band`, `.band.thick`): the modules one call passes through. Before: thin bands that each forward or rename; after: one thick band naming the consolidated responsibility.
- **Mass** (`.mass`, `.pair`, `.iface`, `.impl`): interface as wide as implementation. Set each bar's height. Before: the interface bar nearly as tall as the implementation; after: short against tall.

## Tone

Plain English, concise, but the architectural nouns and verbs come straight from the `/codebase-design` skill. Concision is not an excuse to drift.

**Use exactly:** module, interface, implementation, depth, deep, shallow, seam, adapter, leverage, locality.

**Never substitute:** component, service, unit (for module) · API, signature (for interface) · boundary (for seam) · layer, wrapper (for module, when you mean module).

**Phrasings that fit the style:**

- "Order intake module is shallow — interface nearly matches the implementation."
- "Pricing leaks across the seam."
- "Deepen: one interface, one place to test."
- "Two adapters justify the seam: HTTP in prod, in-memory in tests."

**Wins bullets** name the gain in glossary terms: *"locality: bugs concentrate in one module"*, *"leverage: one interface, N call sites"*, *"interface shrinks; implementation absorbs the wrappers"*. Don't write *"easier to maintain"* or *"cleaner code"* — those terms aren't in the glossary and don't earn their place.

No hedging, no throat-clearing, no "it's worth noting that…". If a sentence could be a bullet, make it a bullet. If a bullet could be cut, cut it. If a term isn't in the `/codebase-design` glossary, reach for one that is before inventing a new one.
