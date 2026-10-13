# Fusion API: the call behind each tool

Reference for scripts written against the Fusion Python API. Lengths passed as numbers are in
centimetres; an expression string (`'12 mm'`, `'wall * 2'`) carries its own units.

## Sketch tools

| Tool | Call | Then |
|---|---|---|
| Centre rectangle | `sketchLines.addCenterPointRectangle(centre, corner)` | The centre is a bare `Point3D`, so nothing ties it down: add a coincident or symmetry constraint, then a width and a height |
| Two-point rectangle | `sketchLines.addTwoPointRectangle(p1, p2)` | Width and height |
| Polygon | `sketchLines.addScribedPolygon(centre, edgeCount, angle, radius, isInscribed)` | Size and angle are bare numbers and create no dimensions. Add `geometricConstraints.addPolygon(lines)` if it is not already there, an offset dimension across two opposite flats, and an angular dimension from one flat to a projected axis |
| Slot | `sketch.addCenterToCenterSlot(start, end, width, createWidthDimension)`, also `addOverallSlot`, `addCenterPointSlot` | Shape constraints come with it; `width` takes an expression |
| Circle | `sketchCircles.addByCenterRadius(point, r)` | `addDiameterDimension` |

## Constraints

`geometricConstraints`: `addCoincident`, `addCollinear`, `addParallel`, `addPerpendicular2`,
`addHorizontal`, `addVertical`, `addHorizontalPoints`, `addVerticalPoints`, `addConcentric`,
`addTangent`, `addEqual`, `addMidPoint(point, curve)`, `addSymmetry(a, b, line)`,
`addOffset2(createOffsetInput(curves, offset))`, `addRectangularPattern`, `addCircularPattern`.

There is no sketch mirror. Mirror with `addSymmetry` per pair, or with a mirror feature.

## Dimensions

`sketchDimensions`: `addDistanceDimension(a, b, orientation, textPoint)` between any two sketch
points, `addOffsetDimension(line, lineOrPoint, textPoint)`, `addDiameterDimension`,
`addRadialDimension`, `addAngularDimension(l1, l2, textPoint)` (the text point picks the
quadrant), `addConcentricCircleDimension`.

Set a value with `dim.parameter.expression = '12 mm'` or `= 'rail_h'`. Rename the dimension's
own parameter with `dim.parameter.name = 'hook_reach'` when a person will look for it.

## Construction and reference geometry

- `curve.isConstruction = True`, `line.isCenterLine = True`.
- `sketch.project2([entity], isLinked)[0]` brings in one edge, one earlier sketch point or
  curve, or a construction axis to measure an angle from. It returns a list.
  `sketch.project` is retired.
- `sketches.addWithoutEdges(face)` puts a sketch on a face without its outline. `sketches.add`
  on a face projects every edge.
- `constructionPlanes.createInput()` then `setByOffset(plane, offset)` or
  `setByTwoPlanes(a, b)` for a midplane.

## Features

- Holes: `holeFeatures.createSimpleInput(d)`, `createCounterboreInput(d, cbD, cbDepth)`,
  `createCountersinkInput(d, csD, angle)`; `input.setToClearanceHole(...)`,
  `setToTappedHole(...)`. Place with `setPositionBySketchPoints(points)`. A hole placed by a
  `Point3D` is not associative.
- `rectangularPatternFeatures`, `circularPatternFeatures` and `mirrorFeatures` take features as
  well as bodies. One kind of entity per collection.
- `timelineGroups.add(startIndex, endIndex)` groups a contiguous run, and the group takes a
  `.name`. Groups do not nest. Add them last and from the end of the timeline backwards: each
  one collapses its items into a single timeline entry and shifts every later index. A group
  has no health of its own; read its items through `TimelineGroup.cast(item).item(i)`.
- A sketch, a plane and a feature each take their name from `.name`, which is also the
  timeline name. Names are unique: a feature given its sketch's name becomes `name (1)`, so
  name the sketch for the thing and the feature for the action (`hook`, `hook extrude`).
- An extrude from a sketch on a face runs along the face's normal, out of the solid. Cut with
  the negative direction.

## Where the API differs from the UI

- **Adding a sketch point level with an earlier one, then dimensioning it from the origin,
  fails with `VCS_SKETCH_OVER_CONSTRAINTS`.** Fusion has already lined the two up. Tie the new
  point to the earlier one with `addHorizontalPoints` or `addVerticalPoints`, and give it only
  the dimension that is left.
- A sketch on a face may have its axes turned: its horizontal can be the model's Y. Choose
  horizontal or vertical for a dimension from `sketch.xDirection`, not from the model axis.
- A text box holds some of its own sides square and refuses a horizontal or vertical
  constraint on those. Try each side, then give it a position and whatever size is still free.
- `sketch.referencePlane` raises for a sketch on a face unless the timeline is rolled back to
  that sketch.
- `sketch.isFullyConstrained` says whether a sketch is constrained. The per-entity
  `isFullyConstrained` finds the loose curve.
- `sketch.isComputeDeferred = True` speeds up adding many curves. Set it back before any
  feature uses the sketch.
- A script run through the Fusion MCP keeps its globals between calls. Define `run(context)`
  and put the work inside it, so an older script's `run` is not called again.
- `app.documents` includes every referenced document. Find one by name; do not assume the
  active document is the one just built.
