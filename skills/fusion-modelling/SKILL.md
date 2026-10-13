---
name: fusion-modelling
description: >
    Model in Autodesk Fusion the way a disciplined person would, so the result can be opened, read
    and changed by hand. Use when writing or changing a script that builds a Fusion design, when
    building or editing a part or an assembly through the Fusion MCP, when adding joints,
    fasteners or bought-in parts, or when reviewing a Fusion model's sketches, parameters,
    joints or timeline.
---

# fusion-modelling

A model is finished when a **stranger** can work on it: someone who opens it in Fusion a month
from now, knowing nothing about how it was made, finds the sketch they want, reads it, changes
one thing, and nothing later in the timeline breaks.

Every rule below serves the stranger. They are guidance and a matter of taste, with no numeric
limits: when a rule and the stranger's convenience disagree, the stranger wins.

Writing against the Fusion API: read [`API.md`](API.md) first, for the call behind each native
tool and the places the API behaves differently from the UI.

## Structure

1. **One component per part, created first.** Everything is built inside it. A document that
   holds several parts gives each its own component.
2. **The origin is the functional datum**: the mounting face, the hole or the centre line the
   part is located by. A symmetric part is centred on it. Use the back-bottom-left corner only
   for something with no datum, such as a piece of furniture.
3. **The timeline reads logically.** Anything appears before what depends on it, and related
   features sit together in a named group, left expanded so the whole timeline is in view.
   There is no fixed order of stages, with one exception: fillets and chamfers come after the geometry they round, and nothing references
   what they produce.
4. **One idea per sketch and per feature.** A body, a hook and a row of holes are three sketches
   and three features, so changing the hook touches only the hook. Draw a compound outline only
   when the shape really is one thing.

## References

5. **Where a sketch sits.** Main solids sit on origin planes or planes offset from them. A
   detail feature may sit on a planar face of a main solid when it belongs to that face, such
   as a hole or a deboss. Leave the face's outline out of the sketch.
6. **What a sketch's dimensions measure from**, in order of preference:
    1. the datum, when it is the natural reference;
    2. geometry from the earlier sketch that made the solid;
    3. one edge of a main solid, projected on purpose, when the distance to that edge is the
       point: an edge margin, a wall thickness.

   Vertices, a face's automatic outline, and anything a fillet or chamfer produced are not
   references.

## Sketching

7. **Use the native tools**: rectangle, slot, polygon, circle. A hexagon is the polygon tool,
   one size and one angle. A rectangle drawn as four lines is not the rectangle tool.
8. **Constraints before dimensions.** Centre with symmetry or midpoint; repeat with equal;
   join with tangent and coincident. A dimension is what is left over.
9. **Dimension what you would measure**: a width, a hole spacing, a wall, a distance from the
   datum. A rectangle is a width and a height, and its four corners follow.
10. **One visible dimension for anything a person would turn, move or resize.** If the stranger
    wants to rotate the nut pocket, there is an angle to change.
11. **Fully constrained, by real constraints.** The fix constraint is reserved for generated
    geometry too large to constrain by hand, such as a field of hundreds of slots, and the
    sketch's name says so.

## Parameters

12. **A named parameter is for a value that is shared, measured, bought-in or a house
    default**: used by more than one feature, read off a real object, set by a fastener or a
    standard, or common to every part. Everything else is a plain number on the sketch, and a
    plain number used once is correct, not an omission. Bought-in means the one or two sizes
    another part mates to, such as a thread or a pin spacing, not every size in the catalogue.
    A parameter names a size, never a coordinate, and one that nothing uses is removed.
13. **The document owns the values.** A script adds the parameters that are missing and leaves
    the rest as it finds them, so a value tuned in Fusion survives a rebuild. It reports every
    parameter that differs from its own default.

## Features

14. **Fastener holes are hole features**, placed by a sketch point. **Repeats are pattern or
    mirror features.** Sketch geometry is for shapes no feature makes, such as a nut pocket.
15. **Name everything**: sketches, planes, features, bodies and groups, by what they are for.
16. **Edit, don't redraw.** When changing an existing model by hand or through the MCP, change
    the sketch that is there. Later features hang off its geometry.
17. **A yellow warning in the timeline is a failure**, the same as a red one. So is an error
    from the API: find what it objects to and fix that, and never catch it and carry on.

## Bought-in parts

18. **Never model a bolt, screw, nut or washer.** They come from Fusion's fastener library
    (Insert > Insert Fastener). The model's job is the hole: a hole feature of the right
    clearance or thread in every part the fastener passes through, lined up, so the fastener
    has something to snap to. Then try to place the fastener, as [`API.md`](API.md) describes.
    When that cannot be done without a person at the dialog, stop and give the user the list
    to insert by hand: each hole by part and feature name, with the fastener, its standard,
    size and length, and any washers and nut.
19. **Any other bought-in part is an envelope with true mating features**: the faces, holes,
    threads and pivots other parts meet, at the maker's dimensions, and a plain block for the
    rest. Use the maker's STEP file when there is one. Check the part exists and can be bought
    before modelling around it.

## Assemblies

An assembly is a document that brings parts together. Its stranger wants to drag the mechanism
and see it move as the real one would, and to swap or resize a part without the rest
scattering.

20. **Parts that physically assemble are joined by joints**: bolted, pinned, sliding, hinged.
    Each joint is the one the real connection is. A bolted face is rigid, a pin is a revolute,
    a rod end is a ball, a carriage on a track is a slider. Never position such a part by a
    transform, and never stand in an invented joint for a mechanism: if the bed is moved by
    two actuators, the actuators move it.
21. **Joints run between the features that meet**: the hole and the pin, the two faces. Model
    those features in the parts first. A joint with typed-in offsets is a coordinate in
    disguise.
22. **Ground one thing**: the part the rest is built on. Everything else is held by its joints,
    with limits where the real travel has them.
23. **A finished sub-assembly placed in a larger scene is positioned exactly and grounded to
    its parent**, with no joints, so it stays put when the scene around it changes. This is
    the layout of the whole vehicle, and normally the user's own work: ask before doing it.
24. **The assembly holds no sizes of its own.** A length that belongs to a part is read from
    that part. A value that exists only in the assembly, such as a pose, is a named parameter
    there or the joint's own drive value.
25. **Edit, don't rebuild.** Changing an assembly changes the joint or the part that is there.
    Deleting every occurrence and joint to place them again throws away whatever the user
    adjusted by hand.
26. **Name every occurrence and joint** for what it is: `left actuator`, `left actuator base
    pin`.

## Documents

27. **Never save over a document the work did not create.** Before making a document, look for
    one of that name in the project. If it is there and is not this script's own earlier
    output, stop and ask for a name.

## Review

A part or an assembly is done when it has had a **blind review** and every finding is either
fixed or shown to the user. This applies to every new one and to every change that adds or
reshapes a feature or a joint: follow [`REVIEW.md`](REVIEW.md).

## Sources

The rules draw on Gebhard's Resilient Modeling Strategy (timeline dependencies, fillets last),
Autodesk University handout PD20709 by Strater and Doering (origin geometry is the most stable
reference; name everything; project sparingly; edit, don't redraw), Autodesk's Fusion sketch
guidance (constraints before dimensions) and public team CAD standards (symmetry over one-sided
dimensions, hole features). Rules 12, 13 and 18 to 27 are the author's own.
