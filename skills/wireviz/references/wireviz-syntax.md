# WireViz YAML Syntax Reference

## Top-level Structure

```yaml
metadata:       # optional
options:        # optional
connectors:     # required (at least one connector)
cables:         # required (at least one cable or bundle)
connections:    # required
additional_bom_items:  # optional
tweak:          # optional
```

---

## `metadata`

All fields optional.

```yaml
metadata:
  title: "My Harness"
  description: "Description text"
  version: "1.0"
  author: "Name"
  revision: "A"
  revision_history: "Initial release"
  project_code: "PRJ-001"
  approval: "Name"
```

---

## `options`

All fields optional.

```yaml
options:
  bgcolor: WH           # background colour (colour code or hex)
  color_mode: SHORT     # full | hex | short | German
  mini_bom_mode: false
  fontname: "JetBrains Mono"
  fontsize: 10
```

---

## `connectors`

Each connector must have at least one of: `pincount`, `pins`, or `pinlabels`.

```yaml
connectors:
  X1:
    # REQUIRED: at least one of these three
    pincount: 4                       # number of pins (auto-numbers them 1..N)
    pins: [1, 2, 3, 4]               # explicit pin numbers
    pinlabels: [GND, VCC, SCL, SDA]  # labels (also sets pincount implicitly)

    # Physical description
    type: Molex KK 254                # connector family/type
    subtype: female                   # male / female / etc.
    color: BK                         # connector body colour (not wire colour)
    bgcolor: WH                       # background colour in diagram

    # Additional data
    manufacturer: Molex
    mpn: 22-01-3047                   # manufacturer part number
    pn: 12345                         # internal part number
    notes: "some note"

    # Display
    show_name: true
    style: simple                     # simple style removes pin numbers box
    hide_disconnected_pins: false

    # Image (optional)
    image:
      src: path/to/image.png
      caption: "Photo"
      height: 100                     # px
      width: 100
      scale: false                    # false | true | width | height | both
      fixedsize: false

    # Wire colours per pin (array, length must match pincount)
    pincolors: [BK, RD, GN, YE]

    # Short pins together (e.g. loop: [[1,2], [3,4]])
    loop: [[1,2]]
```

### Connector with `pins` dict (non-sequential pin numbers)
```yaml
X1:
  pins: {1: GND, 3: VCC, 5: SCL, 7: SDA}
```

---

## `cables`

```yaml
cables:
  W1:
    # REQUIRED: at least wirecount or colors
    wirecount: 4                      # number of wires (required if no colors)
    colors: [BK, RD, GN, YE]        # explicit colours (sets wirecount implicitly)

    # Wire properties
    gauge: 0.25 mm2                   # or "24 AWG" — include units
    color_code: DIN                   # DIN | IEC | T568A | T568B | TEL | TELALT | BW
    show_equiv: true                  # show AWG↔mm² conversion
    length: 1.5                       # in metres (no unit needed)
    shield: false                     # adds wire 's' for shield drain
    wirelabels: [GND, VCC, SCL, SDA] # functional labels (must match wirecount)
    category: bundle                  # 'bundle' = drawn as individual wires not a cable

    # Metadata
    type: "Silicone Multi-conductor"
    manufacturer: Generic
    mpn: ABC123
    pn: W001
    notes: "Assembly notes here"
    bgcolor: IV

    # Image
    image:
      src: path/to/image.png
```

### Wire colour codes

Two-letter codes (case-insensitive in most contexts but use uppercase for reliability):

| Code | Colour        | Code | Colour      |
|------|---------------|------|-------------|
| BK   | Black         | GY   | Grey        |
| BN   | Brown         | WT   | White       |
| RD   | Red           | PK   | Pink        |
| OR   | Orange        | TN   | Tan         |
| YE   | Yellow        | GD   | Gold        |
| GN   | Green         | SV   | Silver      |
| BU   | Blue          | RB   | Rainbow     |
| VT   | Violet        | IV   | Ivory       |
| WH   | White (alt)   | OG   | Orange (alt)|
| GR   | Grey (alt)    | LB   | Light Blue  |
| TQ   | Turquoise     | SL   | Silver (alt)|
| CU   | Copper        |      |             |

**Striped wires** — concatenate two codes: `GNYE` = green/yellow stripe, `BUWH` = blue/white stripe.

**Hex colour** — use `"#FF0000"` for custom colours.

### Standard colour codes (sets colors automatically by index)

- `DIN` — DIN 47100 (BN, RD, OR, YE, GY, WT, BK, VT, GN, BU, RD-BK, BK-BU...)
- `IEC` — IEC 60757
- `T568A` / `T568B` — Ethernet pairs
- `TEL` / `TELALT` — 25-pair telephone
- `BW` — black/white alternating

---

## `connections`

Connections describe how pins on connectors are joined through cable wires.

### Basic structure

Each entry in `connections:` is a list. Each element of that list is either:
- `CONNECTOR_NAME: pins` — a connector with specified pins
- `CABLE_NAME: wires` — a cable with specified wires
- A standalone component (ferrule, intermediate connector) with no explicit pins (uses `.` suffix)

```yaml
connections:
  -                               # each `-` is one connection group
    - X1: [1, 2, 3]              # connector X1 pins 1, 2, 3
    - W1: [1, 2, 3]              # cable W1 wires 1, 2, 3
    - X2: [1, 2, 3]             # connector X2 pins 1, 2, 3
```

Pin/wire references can be:
- **Numbers**: `[1, 2, 3]`
- **Labels**: `[GND, VCC, SCL]` — must exactly match defined labels
- **Range**: `[1-4]` expands to `[1, 2, 3, 4]`
- **Reverse range**: `[4-1]` expands to `[4, 3, 2, 1]`
- **Shield**: `s` (for shielded cables)
- **Single**: `1` (no brackets needed for one pin)

All lists in a single connection group must have the **same length**.

### Single-pin connection

```yaml
connections:
  -
    - X1: 5          # single pin — no brackets
    - W1: 3
    - X2: 2
```

### Shield connection

```yaml
connections:
  -
    - X1: 1
    - W1: s          # 's' is the shield drain wire
```

### Connection with intermediate component (no explicit pin)

Use a trailing `.` to auto-assign. Each `.` creates a new instance of that component.

```yaml
connections:
  -
    - FUSE_PANEL: [CH1]
    - GENERIC_24V.              # creates instance of GENERIC_24V
    - RELAY: [COM]
  -
    - RELAY: [NO]
    - GENERIC_24V.              # creates another instance
    - DEVICE: [VCC]
```

### Splitting a connector into two instances (A-side / B-side)

```yaml
connections:
  -
    - CONNECTOR
    - [==]
    - CONNECTOR.CONNECTOR_B    # creates CONNECTOR_B as a second instance
```

Then continue wiring from `CONNECTOR.CONNECTOR_B`:

```yaml
  -
    - CONNECTOR.CONNECTOR_B: [GND, VCC]
    - W2: [1, 2]
    - DEVICE: [GND, VCC]
```

### Using wire labels as references

If a cable has `wirelabels`, you can reference by label instead of number:

```yaml
cables:
  W1:
    wirecount: 3
    wirelabels: [GND, VCC, SIG]

connections:
  -
    - X1: [GND, VCC, SIG]
    - W1: [GND, VCC, SIG]     # resolved to wire indices 1, 2, 3
    - X2: [1, 2, 3]
```

### YAML anchors for reusing pin/wire lists

```yaml
connectors:
  CONN:
    pinlabels: &LABELS [GND, VCC, SCL, SDA]

cables:
  W1:
    wirelabels: *LABELS       # reuse the same list
```

---

## `additional_bom_items`

Add items to the bill of materials that don't correspond to connectors or cables.

```yaml
additional_bom_items:
  - item: Crimp ferrule 0.5mm²
    qty: 6
    manufacturer: TE Connectivity
    mpn: FE-0.5
    pn: F001
```

---

## `tweak`

Raw GraphViz directives injected into the rendered diagram. Rarely needed.

```yaml
tweak:
  - "graph [rankdir=LR]"
  - "node [shape=box]"
```

---

## YAML Anchor Patterns

### Define a template and reuse it

```yaml
connectors:
  X1: &mytemplate
    type: Molex KK 254
    pinlabels: [GND, VCC]
  X2:
    <<: *mytemplate             # inherits all X1 fields
    subtype: female             # override just this field
```

### Define a shared template block (not a connector itself)

```yaml
templates:
  - &deutsch_8pin
    type: Deutsch DT
    pincount: 8
    bgcolor: OG

connectors:
  SHOWER_CONN:
    <<: *deutsch_8pin
    pinlabels: [SIG1, SIG2, GND, VCC, ...]
```

### Merge shared connector definitions from another file

In the user's project, `shared.yml` defines `&SHARED_CONNECTORS` and each system file merges it:

```yaml
connectors:
  <<: *SHARED_CONNECTORS        # pull in all shared connectors
  MY_NEW_CONN:                  # add system-specific connectors
    type: Deutsch DT
    pincount: 4
```

---

## Complete Minimal Example

```yaml
connectors:
  X1:
    type: Molex KK 254
    subtype: female
    pinlabels: [GND, VCC, SCL, SDA]
  X2:
    type: Molex KK 254
    subtype: male
    pinlabels: [GND, VCC, SCL, SDA]

cables:
  W1:
    gauge: 0.25 mm2
    length: 0.3
    wirecount: 4
    colors: [BK, RD, YE, GN]
    wirelabels: [GND, VCC, SCL, SDA]

connections:
  -
    - X1: [GND, VCC, SCL, SDA]
    - W1: [GND, VCC, SCL, SDA]
    - X2: [GND, VCC, SCL, SDA]
```

---

## Multi-stage Example (fuse → cable → device)

```yaml
connectors:
  FUSE_PANEL:
    pinlabels: [CH1, CH2]
  DEVICE:
    pinlabels: [VCC, GND]
  FUSE_10A:
    style: simple
    type: Blade Fuse
    pincount: 2

cables:
  PWR_CABLE:
    type: Silicone Single-conductor
    gauge: 1 mm2
    colors: [RD]
    wirecount: 1

connections:
  -
    - FUSE_PANEL: [CH1]
    - PWR_CABLE.
    - FUSE_10A.
    - PWR_CABLE.
    - DEVICE: [VCC]
```

---

## Daisy Chain Example

```yaml
connectors:
  X1: &con_template
    type: JST XH
    pinlabels: [GND, VCC, SIG]
  X2:
    <<: *con_template
  X3:
    <<: *con_template

cables:
  W1: &cbl_template
    gauge: 24 AWG
    wirecount: 3
    colors: [BK, RD, YE]
    length: 0.2
  W2:
    <<: *cbl_template

connections:
  -
    - X1: [1-3]
    - W1: [1-3]
    - X2: [1-3]
  -
    - X2: [1-3]
    - W2: [1-3]
    - X3: [1-3]
```
