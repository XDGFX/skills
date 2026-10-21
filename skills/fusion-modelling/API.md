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

`parameter.dependentParameters.count` is zero for a user parameter nothing uses, and
`parameter.deleteMe()` removes it. Repeat until none goes: one unused parameter can be the only
thing using another.

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
  one collapses its items into a single timeline entry and shifts every later index. Set
  `group.isCollapsed = False` on each once they are all made. A group has no health of its own; read its items through `TimelineGroup.cast(item).item(i)`.
- A sketch, a plane and a feature each take their name from `.name`, which is also the
  timeline name. Names are unique: a feature given its sketch's name becomes `name (1)`, so
  name the sketch for the thing and the feature for the action (`hook`, `hook extrude`).
- An extrude from a sketch on a face runs along the face's normal, out of the solid. Cut with
  the negative direction.
- An extrude that starts off its sketch plane: `input.startExtent =
  OffsetStartDefinition.create(ValueInput.createByString(expr))`. Prefer a named offset plane
  when the height is something a person would look for.
- `timelineGroups.add(i, i)` raises "At least 2 features needed for a group". A stage with one
  feature goes ungrouped, under a name that says what it is.

## Assemblies

- **Insert a part**: `occurrences.addByInsert(dataFile, Matrix3D.create(), True)` links the
  saved document. A linked insert needs both documents saved in the same project. The
  transform is only where it lands before its joint moves it.
- **Insert under a sub-component**: `parent.component.occurrences.addByInsert(f, m,
  True).createForAssemblyContext(parent)`.
- **Joints**: `joints.createInput(geoOne, geoTwo)`, then `setAsRigidJointMotion()`,
  `setAsRevoluteJointMotion(axis)`, `setAsSliderJointMotion(axis)`, `setAsBallJointMotion(...)`
  and so on, then `joints.add(input)`. Build each `JointGeometry` from the real feature:
  `JointGeometry.createByCylinderOrConeFace` for a hole or pin,
  `createByPlanarFace`, `createByPoint`. Take the face from the occurrence's proxy
  (`occ.bRepBodies`, or `entity.createForAssemblyContext(occ)`), not from the component.
- `asBuiltJoints` joins two occurrences where they already sit. Use it only for parts placed by
  something real, such as a sub-assembly imported whole.
- Ground with `occ.isGroundToParent = True`. Rule 23's sub-assemblies are the case for it, with
  an exact transform.
- Name with `occ.component.name` for a part made in place, and `joint.name`. An inserted
  document's occurrence takes the document's name and a `:n` suffix that cannot be changed, so
  the joint's name carries which one it is.
- Joint limits (`joint.jointMotion.slideLimits.minimumValue`) take numbers in centimetres or
  radians, not expressions. A drive value takes an expression: `joint.angle.expression`.
- Linked instances of one sub-assembly share its internal joints, so they move together.
  `occ.breakLink()` gives an instance its own.
- After a part is rebuilt, joints to its old faces fail. Repoint them, or delete last-first:
  deleting in timeline order raises `VCS_JOINT_FAILURE`.
- **Delete a jointed mechanism in one step**: `design.timeline.moveToBeginning()` then
  `design.timeline.deleteAllAfterMarker()`, with no solves. Deleting joint by joint makes Fusion
  re-solve the half-dismantled assembly after each `joint.deleteMe()`, and with limited ball
  joints that locked it up for good. This is how a script-owned assembly is rebuilt (rule 25).
- Fusion's interference check gives nonsense on linked or referenced components: it treats
  them as sitting at their origins. Intersect copies of the bodies instead
  (`TemporaryBRepManager`), over an explicit list of bodies.
- Setting one joint's drive value lets every other free joint drift, and a joint cannot be
  locked through the API. To pose something by parameter, a rigid `Joint` (not as-built) with
  `joint.angle.expression = '<user parameter>'` holds. A joint made on a sketch point turns
  about that sketch's normal.
- `DataFile.deleteMe()` on an unused document raised `InternalValidationError` every time one
  session tried it. Ask the user to remove documents.

## Fasteners

There is no fastener API: the library has no insert call. What the API has, as a preview that
may change:

- `occurrence.definition` is a `FastenerOccurrenceDefinition` for a library fastener, with
  `isSizeUpToDate` and `updateSize()`. Use it to check what the user placed.
- The commands exist and open their dialogs: `ui.commandDefinitions.itemById(
  'FusionFastenersCommand').execute()` for Insert Fastener, also `FastenersInsertSimilar`,
  `FastenersAllStacksUpdateCmd`. A script cannot fill in another command's dialog.
- The fasteners a hub has used are ordinary documents in its `Standard Components` project,
  under `Fasteners/<kind>/<standard>` (for example `Fasteners/Nuts/DIN 934`), reachable through
  `app.data.dataProjects`.

To attempt a placement: look in `dir(adsk.fusion)` and on `Occurrences` for an insert method
that has arrived since this was written, and use it if there is one. If there is not, do not
substitute `addByInsert` of a library file with a hand-made joint: it has not been shown to
produce a real fastener, and one trial of it hung Fusion. Hand the user the list rule 18 asks
for. They insert the first of each kind; Insert Similar does the rest.

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
  `isFullyConstrained` finds the loose curve. A sketch containing text never reports fully
  constrained: check its curves one by one.
- `sketch.isComputeDeferred = True` speeds up adding many curves. Set it back before any
  feature uses the sketch.
- The Fusion MCP runs a script's top-level code, then calls `run(context)` if the script
  defines one, and keeps globals between calls. Most part scripts work at the top level and
  are `exec`'d from a one-line wrapper; either way works.
- `app.documents` includes every referenced document, and can hold stale versions of one
  under the same name. Find a document by name and keep the one where
  `d.dataFile.versionNumber == d.dataFile.latestVersionNumber`; do not assume the active
  document is the one just built. A saved document's `name` ends in its version (`rack v18`):
  compare without it, or against `d.dataFile.name`.
- `app.activeDocument` is `None` when nothing is open.
- A document made by importing, with no timeline, has no parameters: `design.userParameters`
  raises "this is not a parametric design". Check `design.designType`.
- `materialLibrary.appearances.itemByName(name)` raises `invalid name` for a name that is not
  there, where most `itemByName` calls return `None`.
- Reported once and not confirmed: `occurrences.addByInsert` intermittently raising
  `InternalValidationError : group`, cured by running again; and Fusion hanging when a ball
  joint's yaw axis is changed on an existing joint.

## Calling Fusion

- **A timeout means still running.** The MCP gives up on a call after about a minute, and the
  script carries on in Fusion. After "The operation timed out", send no retry and no other
  Fusion call, read-only ones included (they queue behind it), until the script's progress file
  says done. If the file stops moving, Fusion is stuck or waiting on a dialog: stop and tell the
  user.
- **Size each call to finish in 30 to 40 seconds**, writing its progress to a file: one stage of
  a build per call, one angle band per call for a sweep, and an interference check over an
  explicit list of bodies.
- **Every stage is idempotent.** Before building, a stage deletes or replaces its own components
  and bodies by name, so a call that lands late plus a re-run never leaves `name (1)` copies.
- **Scope every read** to the active document or to named components. A scan of every body in
  every open document hung Fusion for 15 minutes.
- **Script output is lost**: what a script prints comes back as `"message": ""`. Write what you
  need to a file and read that.

### The gate

Fusion runs one call at a time, and two sessions' calls at once can swap each other's output.
The `fusion-gate` plugin enforces this with a hook on every Fusion call, so don't ask other
sessions whether Fusion is free.

- A call refused with "…NOT run" never reached Fusion, and a refusal keeps your lease. The
  refusal gives the `fusion_gate.py acquire` command that waits your turn. Don't work around it.
- Before a multi-step job, run `acquire` with a reason, an estimate and `-d` listing every
  document you will open, read or change. Agents whose documents don't overlap take turns call
  by call. Without `-d` you hold all of Fusion and everyone else waits.
- While you share Fusion, the gate makes one of your documents active before each call and
  reads it back. A script may find your own documents by name in `app.documents`; it is refused
  if it names another agent's document, and if it changes one anyway the gate stops you until
  you release. Open another of your documents with `fusion_mcp_execute` `document` `open`.
- Documents your calls create or open become yours until you release, so save a new one under
  its name in the call that creates it. Never save or close a document the gate lists.
- A subagent is a holder of its own. Put `FUSION_GATE_AGENT=<agent_id>` before each of its
  `acquire` and `release` commands; the gate gives the id the first time one is sent without it.
- Run `release` once the work is saved. `status` marks a holder whose session has gone as
  STALE; `release --stale` frees it. When the user wants Fusion to themselves, `pause` and
  `resume` hand it over and back.
- Any installed copy of the script reaches the same gate:
  `python3 "$(ls -d ~/.claude/plugins/cache/xdgfx/fusion-gate/*/ | sort -V | tail -1)fusion_gate.py" status`.
