"""pdf2step: build 3D STEP models of plate-type parts from a vector PDF drawing.

usage: python pdf2step.py drawing.pdf --page 13 --scale 3 [-o outdir] [--units inch|mm]
Each part on the page needs a plan view and at least one side view next to it (third-angle)."""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract import page_prims
from views import find_views, dominant_angle, rotate_prims
from recognize import group_parts, recognize
from build import build, export


def run(pdf, page, scale, out, units="inch"):
    prims, size = page_prims(pdf, page - 1)
    views = find_views(prims, size)
    parts = group_parts(views)
    used = {id(v) for pl, sd, bt in parts for v in [pl, bt] + [x for _, x in sd] if v is not None}
    unused = [v for v in views if id(v) not in used and not v.iso and v.w * v.h > 0.01]
    # views drawn turned on the sheet: straighten each group of same-angle views and pair them again
    tilted = {}
    for v in unused:
        a = dominant_angle(v)
        if abs(a) > 1.0:
            key = next((k for k in tilted if abs(k - a) < 0.5), a)
            tilted.setdefault(key, []).append(v)
    rotated_parts = []
    thin_all = [p for p in prims if p.cls == "thin"]
    for a, vs in tilted.items():
        sub = rotate_prims([p for v in vs for p in v.prims] + thin_all, -a)
        rviews = find_views(sub, (1e3, 1e3))
        for pr in group_parts(rviews):
            rotated_parts.append((pr, a))
            used.update(id(v) for v in vs)
    parts = [(p_, 0.0) for p_ in parts] + rotated_parts
    skipped = [repr(v) for v in unused if id(v) not in used and v.w * v.h > 0.05]
    os.makedirs(out, exist_ok=True)
    results = []
    for k, ((plan, side, bottom), turned) in enumerate(parts, 1):
        name = f"p{page:02d}_{k:02d}"
        try:
            pm = recognize(plan, side, bottom, scale)
            body = build(pm)
            s = export(body, os.path.join(out, name + ".step"), to_mm=units == "inch")
            bb = s.BoundingBox()
            if abs(turned) > 0:
                pm.notes.append(f"drawn turned {turned:.2f} deg on the sheet - straightened")
            results.append(dict(name=name, size_mm=[round(bb.xlen, 2), round(bb.ylen, 2), round(bb.zlen, 2)],
                                holes=len(pm.holes), side_holes=len(pm.side_holes), cuts=len(pm.cuts), notes=pm.notes,
                                views=[repr(plan)] + [f"{w}: {v!r}" for w, v in side] + ([f"bottom: {bottom!r}"] if bottom else [])))
        except Exception as e:
            results.append(dict(name=name, error=f"{type(e).__name__}: {e}", views=[repr(plan)]))
    if skipped:
        results.append(dict(name=f"p{page:02d}_unused_views", note="no aligned side view found - not built", views=skipped))
    with open(os.path.join(out, f"p{page:02d}_report.json"), "w") as f:
        json.dump(results, f, indent=1)
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--scale", type=float, required=True, help="drawing scale denominator, e.g. 3 for 1:3")
    ap.add_argument("-o", "--out", default="pdf2step_out"); ap.add_argument("--units", default="inch")
    a = ap.parse_args()
    for r in run(a.pdf, a.page, a.scale, a.out, a.units):
        print(json.dumps(r))
