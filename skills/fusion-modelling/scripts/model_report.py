"""Describe a Fusion design as a stranger would find it: parameters, timeline, and each sketch's
shapes, constraints and dimensions. Read-only unless PICTURES is set.

Run through the Fusion MCP from a wrapper, which sets the inputs and prints the result:

    def run(context):
        ns = {'DOC': 'my_part', 'PICTURES': '/some/dir'}   # both optional
        exec(open('<this file>').read(), ns)
        print(ns['report'])

DOC names an open document (default: the active one). PICTURES is a directory to save one image
per sketch into, looking square at it with its dimensions shown; that changes what is visible and
the camera, so the wrapper must not be marked read-only when it is set.
"""

import collections
import math
import re

import adsk.core
import adsk.fusion

HEALTH = {0: 'ok', 1: 'WARNING', 2: 'ERROR', 3: 'unknown', 4: 'suppressed', 5: 'rolled back'}


def kind(obj):
    return obj.objectType.split('::')[-1]


def mm(value):
    return '%.4g' % (value * 10)


def sketch_home(sk):
    try:
        ref = sk.referencePlane
    except RuntimeError:  # a face can only be read with the timeline rolled back to the sketch
        return 'a face of a solid'
    k = kind(ref)
    if k == 'ConstructionPlane':
        return 'plane "%s"' % ref.name
    if k == 'BRepFace':
        return 'face of body "%s"' % ref.body.name
    return k


def describe_sketch(sk, out):
    curves = collections.Counter()
    construction = reference = fixed = 0
    for c in sk.sketchCurves:
        curves[kind(c).replace('Sketch', '').lower()] += 1
        construction += c.isConstruction
        reference += c.isReference
        fixed += c.isFixed
    loose = sum(1 for p in sk.sketchPoints if not p.isReference and p.connectedEntities is None)
    cons = collections.Counter(kind(c).replace('Constraint', '') for c in sk.geometricConstraints)
    out.append('      on %s; %s' % (sketch_home(sk), 'fully constrained' if sk.isFullyConstrained else 'NOT fully constrained'))
    out.append('      curves: %s%s' % (
        ', '.join('%d %s' % (n, k) for k, n in sorted(curves.items())) or 'none',
        ''.join(', %d %s' % (n, label) for n, label in ((construction, 'construction'), (reference, 'projected'), (fixed, 'FIXED'), (loose, 'free points'), (sk.sketchTexts.count, 'text')) if n)))
    out.append('      constraints: %s' % (', '.join('%d %s' % (n, k) for k, n in sorted(cons.items())) or 'none'))
    sources = collections.Counter()
    for c in list(sk.sketchCurves) + list(sk.sketchPoints):
        if c.isReference:
            try:
                src = c.referencedEntity
                sources['%s "%s"' % (kind(src), src.name) if hasattr(src, 'name') else kind(src)] += 1
            except RuntimeError:
                sources['something no longer there'] += 1
    if sources:
        out.append('      projected from: %s' % ', '.join('%d %s' % (n, k) for k, n in sorted(sources.items())))
    origin = sk.originPoint
    from_origin = 0
    dims = []
    for d in sk.sketchDimensions:
        p = d.parameter
        ends = []
        for attr in ('entityOne', 'entityTwo', 'line', 'entity'):
            e = getattr(d, attr, None)
            if e is not None:
                what = kind(e).replace('Sketch', '').lower()
                ends.append('origin' if e == origin else 'projected ' + what if getattr(e, 'isReference', False) else what)
        if any(end == 'origin' or end.startswith('projected') for end in ends):
            from_origin += 1
        literal = re.fullmatch(r'-?[\d.]+ *(mm|cm|in|deg)?', p.expression.strip()) is not None
        dims.append('        %s = %s%s  [%s%s]' % (
            p.name, p.expression, '' if literal else '  (= %s mm)' % mm(p.value) if p.unit != 'deg' else '',
            kind(d).replace('Sketch', '').replace('Dimension', '').lower(), ': ' + ' to '.join(ends) if ends else ''))
    out.append('      dimensions: %d, of which %d measure from the origin or a projected reference' % (len(dims), from_origin))
    out.extend(dims)


def describe_feature(f, out):
    bits = []
    try:
        profs = f.profile
        profs = [profs] if not hasattr(profs, 'count') else list(profs)
        names = sorted({pr.parentSketch.name for pr in profs if hasattr(pr, 'parentSketch')})
        if names:
            bits.append('from sketch %s' % ', '.join('"%s"' % n for n in names))
    except Exception:
        pass
    for label, path in (('distance', ('extentOne', 'distance', 'expression')), ('symmetric about the sketch, total', ('symmetricExtent', 'distance', 'expression')),
                        ('diameter', ('holeDiameter', 'expression')), ('offset', ('definition', 'offset', 'expression'))):
        try:
            v = f
            for a in path:
                v = getattr(v, a)
            bits.append('%s %s' % (label, v))
        except Exception:
            pass
    try:
        ops = adsk.fusion.FeatureOperations
        bits.append({ops.NewBodyFeatureOperation: 'new body', ops.JoinFeatureOperation: 'join', ops.CutFeatureOperation: 'cut', ops.IntersectFeatureOperation: 'intersect'}.get(f.operation, ''))
    except Exception:
        pass
    try:
        bits.append('%d edges' % sum(es.edges.count for es in f.edgeSets))
    except Exception:
        pass
    if bits:
        out.append('      %s' % '; '.join(b for b in bits if b))


def picture(app, sk, others, path):
    for s in others:
        s.isVisible = s == sk
    sk.areDimensionsShown = True
    sk.areConstraintsShown = True
    # frame what was drawn, not a projected axis running off to infinity
    own = [p.geometry for p in sk.sketchPoints if not p.isReference] or [p.geometry for p in sk.sketchPoints]
    xs, ys = [g.x for g in own], [g.y for g in own]
    centre = sk.sketchToModelSpace(adsk.core.Point3D.create((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0))
    span = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    normal = sk.xDirection.crossProduct(sk.yDirection)
    vp = app.activeViewport
    cam = vp.camera
    cam.cameraType = adsk.core.CameraTypes.OrthographicCameraType
    cam.target = centre
    cam.eye = adsk.core.Point3D.create(centre.x + normal.x * 100, centre.y + normal.y * 100, centre.z + normal.z * 100)
    cam.upVector = sk.yDirection
    cam.isSmoothTransition = False
    cam.isFitView = False
    cam.viewExtents = max(span * 0.9, 1.5)
    vp.camera = cam
    vp.refresh()
    adsk.doEvents()
    vp.saveAsImageFile(path, 1100, 850)


def build_report(doc_name=None, pictures=None):
    app = adsk.core.Application.get()
    doc = app.activeDocument
    if doc_name:
        doc = next(d for d in app.documents if d.name == doc_name)
        if pictures:
            doc.activate()
    des = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    out = ['DESIGN %s' % doc.name]

    comps = [c for c in des.allComponents]
    out.append('COMPONENTS %d: %s' % (len(comps), ', '.join('"%s" (%d bodies: %s)' % (c.name, c.bRepBodies.count, ', '.join(b.name for b in c.bRepBodies)) for c in comps)))

    exprs = [p.expression for p in des.allParameters]
    out.append('USER PARAMETERS %d' % des.userParameters.count)
    for p in des.userParameters:
        uses = sum(1 for e in exprs if re.search(r'\b%s\b' % re.escape(p.name), e)) - (1 if re.search(r'\b%s\b' % re.escape(p.name), p.expression) else 0)
        out.append('  %s = %s  used %d times  # %s' % (p.name, p.expression, uses, p.comment))

    tl = des.timeline
    # a collapsed group is one timeline entry holding its items; an expanded one is not an
    # entry at all, and its items name it as their parent
    items = []
    for i in range(tl.count):
        item = tl.item(i)
        if item.isGroup:
            group = adsk.fusion.TimelineGroup.cast(item)
            items.extend((group.name + ' (collapsed)', group.item(j)) for j in range(group.count))
        else:
            items.append((item.parentGroup.name if item.parentGroup else None, item))
    out.append('TIMELINE %d items in %d groups' % (len(items), tl.timelineGroups.count))
    sketches = []
    current = None
    for group, item in items:
        if group != current:
            current = group
            if group:
                out.append('  [group] "%s"' % group)
        pad = '  ' if group else ''
        try:
            ent = item.entity
        except RuntimeError:
            ent = None
        out.append('%s  %s "%s"%s' % (pad, kind(ent) if ent else '?', item.name,
                                     '' if item.healthState == 0 else '  ' + HEALTH.get(item.healthState, str(item.healthState))))
        if ent is None:
            continue
        if kind(ent) == 'Sketch':
            sketches.append(ent)
            describe_sketch(ent, out)
        else:
            describe_feature(ent, out)

    if pictures:
        shown = [(s, s.isVisible) for s in sketches]
        for n, sk in enumerate(sketches):
            path = '%s/%02d_%s.png' % (pictures, n, re.sub(r'[^A-Za-z0-9]+', '_', sk.name))
            try:
                picture(app, sk, sketches, path)
            except Exception as e:
                out.append('picture failed for "%s": %s' % (sk.name, e))
        for s, v in shown:
            s.isVisible = v
            s.areDimensionsShown = False
            s.areConstraintsShown = False
        out.append('PICTURES one per sketch, in timeline order, in %s' % pictures)
    return '\n'.join(out)


report = build_report(globals().get('DOC'), globals().get('PICTURES'))
