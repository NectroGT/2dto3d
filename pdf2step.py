"""pdf2step: build 3D STEP models of plate-type parts from a vector PDF drawing.

usage: python pdf2step.py drawing.pdf --page 13 --scale 3 [-o outdir] [--units inch|mm]
Each part on the page needs a plan view and at least one side view next to it (third-angle)."""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract import page_prims
from views import find_views, dominant_angle, rotate_prims
from recognize import group_parts, recognize
from build import build, export


def _apply_notes(pm, notes, thin, details=(), side=(), plan=None, scale=1.0):
    from notes import attach, apply_hole
    from text import TAP
    groups, idx = [], []
    for i, h in enumerate(pm.holes):
        for (x, y), r in (h.drawn or []):
            groups.append(dict(x=x, y=y, r=[(r, "")])); idx.append(i)
    sides = []
    for i, h in enumerate(pm.side_holes):
        if h.paper is not None and plan is not None:
            dx, dy = h.direction
            a = (plan.x0 + h.entry[0] / scale, plan.y0 + h.entry[1] / scale)
            b = (a[0] + dx * h.depth / scale, a[1] + dy * h.depth / scale)
            sides.append((i, a, b, h.r / scale, h.paper))
    side_views = []
    for where, v in side:
        horizontal = where in ("below", "above")
        cols = []
        for i, h in enumerate(pm.holes):
            if h.paper:
                cols.append((len(groups), h.paper[0] if horizontal else h.paper[1], max(h.radii_paper)))
                groups.append(dict(x=1e9, y=1e9, r=[(0, "")])); idx.append(i)      # placeholder index for this hole
        side_views.append((v, horizontal, cols))
    hits = attach(notes, thin, groups, sides, details, side_views)
    n = 0
    for (kind, i), info in hits.items():
        if kind == "hole":
            ref = pm.holes[idx[i]]
            sig = sorted(round(r, 3) for r in ref.radii_paper)
            same = [h for h in pm.holes if h.radii_paper and sorted(round(r, 3) for r in h.radii_paper) == sig] \
                if info.get("count", 1) > 1 else [ref]
            for h in same:
                apply_hole(h, info, pm.thickness, pm.notes, "")
                n += 1
        else:
            ref = pm.side_holes[i]
            same = [h for h in pm.side_holes if abs(h.r - ref.r) < 1e-3] if info.get("count", 1) > 1 else [ref]
            for h in same:
                if info.get("thread") in TAP:
                    h.r = TAP[info["thread"]] / 2; h.note = info["thread"]
                if "depth" in info:
                    h.depth = info["depth"]
                n += 1
    if n:
        pm.notes.append(f"hole notes applied to {n} hole(s)")


def run(pdf, page, scale, out, units="inch"):
    prims, size = page_prims(pdf, page - 1)
    notes, text_scale, text_lines = [], None, []
    try:
        from text import read_words, lines, callouts, sheet_scale
        tl = lines(read_words(pdf, page - 1))
        notes, text_scale = callouts(tl), sheet_scale(tl)
        text_lines = tl
    except Exception as e:                       # no Tesseract: geometry only
        print(f"(text not read: {type(e).__name__}: {e})", file=sys.stderr)
    if scale is None:
        if text_scale is None:
            raise SystemExit("could not read the sheet scale - give it with --scale")
        scale = text_scale
    views = find_views(prims, size)
    details = []
    if text_lines:
        from details import find_details
        details = find_details(text_lines, views, scale)
    detail_views = {id(d["view"]) for d in details}
    views_main = [v for v in views if id(v) not in detail_views]
    parts = group_parts(views_main)
    used = {id(v) for pl, sd, bt in parts for v in [pl, bt] + [x for _, x in sd] if v is not None}
    unused = [v for v in views_main if id(v) not in used and not v.iso and v.w * v.h > 0.01]
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
            if notes and not turned:
                _apply_notes(pm, notes, [p for p in prims if p.cls == "thin"], details, side, plan, scale)
            body = build(pm)
            s = export(body, os.path.join(out, name + ".step"), to_mm=units == "inch")
            bb = s.BoundingBox()
            if abs(turned) > 0:
                pm.notes.append(f"drawn turned {turned:.2f} deg on the sheet - straightened")
            threads = sorted({h.note for h in pm.holes + pm.side_holes if getattr(h, "note", None)})
            results.append(dict(name=name, scale=scale, threads=threads, size_mm=[round(bb.xlen, 2), round(bb.ylen, 2), round(bb.zlen, 2)],
                                holes=len(pm.holes), side_holes=len(pm.side_holes), cuts=len(pm.cuts), notes=pm.notes,
                                views=[repr(plan)] + [f"{w}: {v!r}" for w, v in side] + ([f"bottom: {bottom!r}"] if bottom else [])))
        except Exception as e:
            results.append(dict(name=name, error=f"{type(e).__name__}: {e}", views=[repr(plan)]))
    if details:
        results.append(dict(name=f"p{page:02d}_details", details=[f"DETAIL {d['name']} x{d['k']:g} on {d['parent']!r}" for d in details]))
    if skipped:
        results.append(dict(name=f"p{page:02d}_unused_views", note="no aligned side view found - not built", views=skipped))
    with open(os.path.join(out, f"p{page:02d}_report.json"), "w") as f:
        json.dump(results, f, indent=1)
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--scale", type=float, default=None,
                    help="drawing scale, e.g. 3 for 1:3 (read from the title block when left out)")
    ap.add_argument("-o", "--out", default="pdf2step_out"); ap.add_argument("--units", default="inch")
    a = ap.parse_args()
    for r in run(a.pdf, a.page, a.scale, a.out, a.units):
        print(json.dumps(r))
