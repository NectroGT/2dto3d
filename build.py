"""PartModel -> CadQuery solid -> STEP"""
import cadquery as cq


import math


def _ok_arc(s):
    (x0, y0), (xm, ym), (x1, y1) = s[1], s[2], s[3]
    cr = (xm - x0) * (y1 - y0) - (ym - y0) * (x1 - x0)
    return abs(cr) > 1e-7 and math.dist(s[1], s[3]) > 1e-4 and math.dist(s[1], s[2]) > 1e-4 and math.dist(s[2], s[3]) > 1e-4


def _wire(segs, arcs=True):
    segs = [s for s in segs if math.dist(s[1], s[-1]) > 1e-6]
    wp = cq.Workplane("XY").moveTo(*segs[0][1])
    for s in segs:
        if s[0] == "line" or not arcs or not _ok_arc(s):
            if s[0] == "arc" and arcs:
                wp = wp.lineTo(*s[2])
            wp = wp.lineTo(*s[-1])
        else:
            wp = wp.threePointArc(s[2], s[3])
    return wp.close()


def _solid(segs, h):
    if len(segs) < 2:
        raise ValueError("empty ring")
    try:
        return _wire(segs).extrude(h)
    except Exception:
        return _wire(segs, arcs=False).extrude(h)


def build(pm):
    T = pm.thickness
    if pm.base and pm.raised:
        body = _solid(pm.outline, pm.base)
        for rz in pm.raised:
            try:
                pad = _solid(rz.ring, rz.z_top)
                for inner in rz.inner:
                    try:
                        pad = pad.cut(_solid(inner, rz.z_top))
                    except Exception:
                        pass
                body = body.union(pad)
            except Exception as e:
                pm.notes.append(f"raised area skipped ({type(e).__name__})")
    else:
        body = _solid(pm.outline, T)
    for c in pm.cuts:
        d = c.depth if c.depth else T
        z0 = 0 if c.side in ("thru", "bottom") else T - d
        h = T if c.side == "thru" else d
        try:
            body = body.cut(_solid(c.ring, h).translate((0, 0, z0)))
        except Exception as e:
            pm.notes.append(f"cut-out skipped ({type(e).__name__})")
    for h in pm.holes:
        for r, side, depth in sorted(h.steps):
            d = depth if depth else T * 0.5
            if side == "thru":
                z0, hh = 0, T
            elif side == "top":
                z0, hh = T - d, d
            else:
                z0, hh = 0, d
            try:
                body = body.cut(cq.Workplane("XY").workplane(offset=z0).center(h.x, h.y).circle(r).extrude(hh))
            except Exception as e:
                pm.notes.append(f"hole at ({h.x}, {h.y}) skipped ({type(e).__name__})")
    for sh in pm.side_holes:
        (x, y), (dx, dy) = sh.entry, sh.direction
        try:
            cyl = cq.Solid.makeCylinder(sh.r, sh.depth + 0.02, cq.Vector(x - 0.02 * dx, y - 0.02 * dy, sh.z), cq.Vector(dx, dy, 0))
            body = body.cut(cq.Workplane().add(cyl))
        except Exception as e:
            pm.notes.append(f"side hole skipped ({type(e).__name__})")
    return body


def export(body, path, to_mm=True):
    s = body.val()
    if to_mm:
        s = s.scale(25.4)
    cq.exporters.export(cq.Workplane().add(s), path)
    return s
