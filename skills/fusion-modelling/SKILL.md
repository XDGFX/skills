---
name: fusion-modelling
description: >
    Model in Autodesk Fusion the way a disciplined person would, so the result can be opened, read
    and changed by hand. Use when writing or changing a script that builds a Fusion design, when
    building or editing a model through the Fusion MCP, or when reviewing a Fusion model's
    sketches, parameters or timeline.
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

1. **One component per part, created first.** Everything is built inside it.
2. **The origin is the functional datum**: the mounting face, the hole or the centre line the
   part is located by. A symmetric part is centred on it. Use the back-bottom-left corner only
   for something with no datum, such as a piece of furniture.
3. **The timeline reads logically.** Anything appears before what depends on it, and related
   features sit together in a named group. There is no fixed order of stages, with one
   exception: fillets and chamfers come after the geometry they round, and nothing references
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
   one size and one angle.
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
    standard, or common to every part. Everything else is a plain number on the sketch.
    A parameter names a size, never a coordinate.
13. **The document owns the values.** A script adds the parameters that are missing and leaves
    the rest as it finds them, so a value tuned in Fusion survives a rebuild. It reports every
    parameter that differs from its own default.

## Features

14. **Fastener holes are hole features**, placed by a sketch point. **Repeats are pattern or
    mirror features.** Sketch geometry is for shapes no feature makes, such as a nut pocket.
15. **Name everything**: sketches, planes, features, bodies and groups, by what they are for.
16. **Edit, don't redraw.** When changing an existing model by hand or through the MCP, change
    the sketch that is there. Later features hang off its geometry.
17. **A yellow warning in the timeline is a failure**, the same as a red one.

## Review

A part is done when it has had a **blind review** and every finding is either fixed or shown to
the user. This applies to every new part and every change that adds or reshapes a feature:
follow [`REVIEW.md`](REVIEW.md).

## Sources

The rules draw on Gebhard's Resilient Modeling Strategy (timeline dependencies, fillets last),
Autodesk University handout PD20709 by Strater and Doering (origin geometry is the most stable
reference; name everything; project sparingly; edit, don't redraw), Autodesk's Fusion sketch
guidance (constraints before dimensions) and public team CAD standards (symmetry over one-sided
dimensions, hole features). Rules 12 and 13 are the author's own.
