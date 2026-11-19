"""Turn the views of one part into features: outline, thickness, holes, cut-outs and pockets."""
import math
from dataclasses import dataclass, field
import numpy as np
from shapely.geometry import LineString, Polygon, Point
from shapely.ops import unary_union, polygonize
from extract import fit_circle


@dataclass
class Hole:
    x: float
    y: float
    steps: list            # [(radius, side, depth)] side "top"/"bottom"/"thru"


@dataclass
class Cut:
    ring: list             # [("line", p0, p1) | ("arc", p0, pm, p1)] in model units
    side: str              # thru / top / bottom
    depth: float


@dataclass
class SideHole:
    entry: tuple           # (x, y) where the hole enters, on the outline
    direction: tuple       # unit (dx, dy) into the part
    z: float
    r: float
    depth: float


@dataclass
class Raised:
    ring: list             # outline of the raised area
    inner: list            # rings of holes in it (list of seg lists)
    z_top: float


@dataclass
class PartModel:
    outline: list
    thickness: float
    holes: list = field(default_factory=list)
    cuts: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    side_holes: list = field(default_factory=list)
    base: float = 0.0              # if > 0: plate thickness outside the raised areas
    raised: list = field(default_factory=list)


# ---------------------------------------------------------------- views -> part groups
def group_parts(views, pair_tol=0.03):
    """plan view + its side views (thin neighbours) + an optional bottom view (mirrored twin below)."""
    used, parts = set(), []
    cands = [v for v in views if not v.iso]
    # of two same-size views stacked vertically the upper one is the plan, the lower its bottom view
    cands.sort(key=lambda v: (-round(v.w * v.h, 1), -v.y0))
    for plan in cands:
        if id(plan) in used:
            continue
        side, bottom = [], None
        small = min(plan.w, plan.h)
        for v in cands:
            if v is plan or id(v) in used:
                continue
            if abs(v.x0 - plan.x0) < pair_tol and abs(v.x1 - plan.x1) < pair_tol:
                d = "below" if v.y1 < plan.y0 else "above"
                if v.h < 0.5 * small:
                    side.append((d, v))
                elif abs(v.h - plan.h) < pair_tol and d == "below" and bottom is None:
                    bottom = v
            elif abs(v.y0 - plan.y0) < pair_tol and abs(v.y1 - plan.y1) < pair_tol and v.w < 0.5 * small:
                side.append(("right" if v.x0 > plan.x1 else "left", v))
        if not side:
            continue
        used.add(id(plan)); used.update(id(v) for _, v in side)
        if bottom is not None:
            used.add(id(bottom))
        parts.append((plan, side, bottom))
    return parts


# ---------------------------------------------------------------- geometry helpers
def _geoms(prims):
    out = []
    for p in prims:
        if p.kind == "circle":
            out.append(LineString([(p.cx + p.r * math.cos(t), p.cy + p.r * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 73)]))
        elif len(p.pts) >= 2:
            out.append(LineString(p.pts))
    return out


def _snap_endpoints(prims, tol):
    """close tiny gaps between line ends so the outline polygonizes"""
    ends = []
    for p in prims:
        if p.kind != "circle":
            ends.append(p.pts[0]); ends.append(p.pts[-1])
    if not ends:
        return
    a = np.array(ends)
    for p in prims:
        if p.kind == "circle":
            continue
        for k in (0, -1):
            d = np.hypot(a[:, 0] - p.pts[k][0], a[:, 1] - p.pts[k][1])
            near = a[(d < tol) & (d > 0)]
            if len(near):
                q = tuple(np.vstack([near, [p.pts[k]]]).mean(0))
                p.pts[k] = q


def faces(prims, snap=0.004):
    _snap_endpoints(prims, snap)
    lines = unary_union(_geoms(prims))
    return list(polygonize(lines))


def circle_of_ring(ring, tol):
    cx, cy, r, rms = fit_circle(np.array(ring.coords)[:-1])
    return (cx, cy, r) if rms < tol and r > 0 else None


def to_segments(coords, circles, tol):
    """ring coordinates -> list of ("line", p0, p1) / ("arc", p0, pm, p1), arcs re-made from source circles"""
    pts = [tuple(c) for c in coords[:-1]]
    n = len(pts)
    on = []
    for p in pts:
        hit = None
        for (cx, cy, r) in circles:
            if abs(math.dist(p, (cx, cy)) - r) < tol:
                hit = (cx, cy, r); break
        on.append(hit)
    segs, i = [], 0
    # start at a vertex that is not inside an arc run
    start = next((k for k in range(n) if on[k] is None or on[k - 1] != on[k]), 0)
    order = [(start + k) % n for k in range(n)] + [start]
    k = 0
    while k < len(order) - 1:
        a = order[k]
        c = on[a]
        j = k + 1
        if c is not None:
            while j < len(order) - 1 and on[order[j]] == c:
                j += 1
            run = order[k:j + 1]
            if len(run) >= 4 and all(on[q] == c for q in run[:-1]) and abs(math.dist(pts[run[-1]], c[:2]) - c[2]) < tol:
                p0, p1 = pts[run[0]], pts[run[-1]]
                pm = pts[run[len(run) // 2]]
                segs.append(("arc", p0, pm, p1))
                k = j
                continue
        segs.append(("line", pts[a], pts[order[k + 1]]))
        k += 1
    # merge collinear lines
    out = []
    for s in segs:
        if out and s[0] == "line" and out[-1][0] == "line":
            p0, p1 = out[-1][1], out[-1][2]
            q1 = s[2]
            v1 = (p1[0] - p0[0], p1[1] - p0[1]); v2 = (q1[0] - p1[0], q1[1] - p1[1])
            cr = v1[0] * v2[1] - v1[1] * v2[0]
            if abs(cr) < 1e-6 * max(1e-9, math.hypot(*v1) * math.hypot(*v2)) * 1e3 and v1[0] * v2[0] + v1[1] * v2[1] > 0:
                out[-1] = ("line", p0, q1); continue
        out.append(s)
    return out


# ---------------------------------------------------------------- depth from a side view
class SideReader:
    """reads feature depths from a side / section view next to the plan view."""
    def __init__(self, where, view, plan, scale):
        self.where, self.v, self.scale = where, view, scale
        self.horizontal = where in ("below", "above")          # shares the plan's x axis
        if self.horizontal:
            self.t0, self.t1 = view.y0, view.y1
            top_at_high = where == "below"                     # third angle: the edge next to the plan is its front face
        else:
            self.t0, self.t1 = view.x0, view.x1
            top_at_high = where == "left"
        self.top_at_high = top_at_high
        self.T = (self.t1 - self.t0) * scale
        self.lines, self.flats, self.vflats = [], [], []
        for p in view.prims + view.thin:
            if p.kind != "line":
                continue
            (x0, y0), (x1, y1) = p.pts[0], p.pts[-1]
            if self.horizontal and abs(x1 - x0) < 1e-3:
                self.lines.append((x0, min(y0, y1), max(y0, y1)))
            if not self.horizontal and abs(y1 - y0) < 1e-3:
                self.lines.append((y0, min(x0, x1), max(x0, x1)))
            if self.horizontal and abs(y1 - y0) < 1e-3:
                self.flats.append((y0, min(x0, x1), max(x0, x1)))
                if p.cls == "visible":
                    self.vflats.append((y0, min(x0, x1), max(x0, x1)))
            if not self.horizontal and abs(x1 - x0) < 1e-3:
                self.flats.append((x0, min(y0, y1), max(y0, y1)))
                if p.cls == "visible":
                    self.vflats.append((x0, min(y0, y1), max(y0, y1)))
        self.lines = self._merge(self.lines)

    @staticmethod
    def _merge(lines, tol=1e-3, gap=0.07):
        """join dashes on the same coordinate into spans"""
        lines.sort(key=lambda s: (round(s[0] / tol), s[1]))
        out = []
        for u, a, b in lines:
            if out and abs(out[-1][0] - u) < tol and a <= out[-1][2] + gap:
                out[-1] = (out[-1][0], min(out[-1][1], a), max(out[-1][2], b))
            else:
                out.append((u, a, b))
        return out

    def floor(self, u1, u2, tol=0.004):
        """model z of a flat (pocket floor) spanning u1..u2, strictly between the faces"""
        best = None
        flats = self._merge([(t, a, b) for t, a, b in self.flats])
        for t, a, b in flats:
            ov = min(b, u2) - max(a, u1)
            if ov < 0.6 * (u2 - u1) or a < u1 - 0.1 * (u2 - u1) - tol or b > u2 + 0.1 * (u2 - u1) + tol:
                continue
            z = (t - self.t0) * self.scale
            if not self.top_at_high:
                z = self.T - z
            if 0.01 < z < self.T - 0.01:
                best = z if best is None else best
        return best

    def z_range(self, u_paper, tol=0.004):
        """model z range (0 = bottom face) covered by feature lines at plan coordinate u"""
        spans = [(a, b) for u, a, b in self.lines if abs(u - u_paper) < tol]
        if not spans:
            return None
        a = min(s[0] for s in spans); b = max(s[1] for s in spans)
        za, zb = (a - self.t0) * self.scale, (b - self.t0) * self.scale
        if not self.top_at_high:
            za, zb = self.T - zb, self.T - za
        return max(0.0, za), min(self.T, zb)


def _cut_depth(readers, u_of, side_hint, T):
    """(side, depth) of a cut-out spanning plan bounds; u_of(reader) -> (u1, u2) in paper units"""
    for rdr in readers:
        u1, u2 = u_of(rdr)
        z = rdr.floor(u1, u2)
        if z is not None:
            if side_hint == "bottom":
                return "bottom", z
            if side_hint == "top":
                return "top", T - z
            a, b = rdr.z_range(u1), rdr.z_range(u2)
            touches_top = any(r and r[1] > T - 0.01 for r in (a, b))
            return ("top", T - z) if touches_top else ("bottom", z)
    return "thru", T


def _material_face(fs):
    """the face whose inner rings are the part's features. Edge-break chamfers draw a second outline just
    inside the first; the thin band between them is skipped."""
    body = max(fs, key=lambda f: Polygon(f.exterior).area)
    for _ in range(4):
        big = [r for r in body.interiors if Polygon(r).area > 0.8 * Polygon(body.exterior).area]
        if not big:
            break
        inner = Polygon(big[0])
        nxt = [f for f in fs if f is not body and abs(Polygon(f.exterior).area - inner.area) < 1e-3 * inner.area + 1e-6]
        if not nxt:
            break
        body = nxt[0]
    return body


# ---------------------------------------------------------------- recognition
def recognize(plan, side, bottom, scale, snap=0.0005):
    notes = []
    readers = [SideReader(w, v, plan, scale) for w, v in side]
    rd = min(readers, key=lambda r: -len(r.lines))
    T = round(rd.T / snap) * snap
    ox, oy = plan.x0, plan.y0
    M = lambda p: (round((p[0] - ox) * scale / snap) * snap, round((p[1] - oy) * scale / snap) * snap)

    # outline and inner boundaries of the plan view
    fs = faces([p for p in plan.prims])
    if not fs:
        raise ValueError("plan view does not close into an outline")
    outer = unary_union(fs)
    if outer.geom_type == "MultiPolygon":
        outer = max(outer.geoms, key=lambda g: g.area)
    body = _material_face(fs)
    circles = [(p.cx, p.cy, p.r) for p in plan.prims if p.kind in ("circle", "arc")]
    ctol = 0.002
    outline = [(k, *[M(q) for q in pts]) for k, *pts in to_segments(list(outer.exterior.coords), circles, ctol)]

    # holes: concentric circles (top view visible, bottom view visible mirrored)
    groups = []
    def add_circle(cx, cy, r, side):
        for g in groups:
            if math.dist((g["x"], g["y"]), (cx, cy)) < 0.003:
                g["r"].append((r, side)); return
        groups.append(dict(x=cx, y=cy, r=[(r, side)]))
    for p in plan.prims:
        if p.kind == "circle" and Point(p.cx, p.cy).within(body.buffer(1e-3)) and p.r < 0.45 * min(plan.w, plan.h):
            add_circle(p.cx, p.cy, p.r, "top")
    # hidden circles (dashed arcs in the plan) are features on the far face: counterbores / holes from below
    arcs = {}
    for p in plan.thin:
        if p.kind in ("arc", "circle") and p.r < 0.45 * min(plan.w, plan.h) and Point(p.cx, p.cy).within(body.buffer(1e-3)):
            k = (round(p.cx / 0.002), round(p.cy / 0.002), round(p.r / 0.001))
            arcs.setdefault(k, []).append(p)
    for k, ps_ in arcs.items():
        sweep = 0.0
        for p in ps_:
            if p.kind == "circle":
                sweep = 360.0; break
            sweep += abs(((p.a1 - p.a0) + 180) % 360 - 180)
        # dashed circles come as several short arcs; a centre-mark or thread arc is a single 270 deg arc
        if sweep >= 200 and (len(ps_) >= 3 or ps_[0].kind == "circle"):
            p = ps_[0]
            hit = next((g for g in groups if math.dist((g["x"], g["y"]), (p.cx, p.cy)) < 0.003), None)
            if hit is not None or True:
                add_circle(p.cx, p.cy, p.r, "bottom")
    # circles drawn as arcs or polylines show up as round inner rings of the material face
    for ring in body.interiors:
        c = circle_of_ring(ring, 0.0015)
        if c and c[2] < 0.45 * min(plan.w, plan.h):
            add_circle(c[0], c[1], c[2], "top")
    if bottom is not None:
        fb0 = faces([p for p in bottom.prims])
        if fb0:
            for ring in _material_face(fb0).interiors:
                c = circle_of_ring(ring, 0.0015)
                if c and c[2] < 0.45 * min(plan.w, plan.h):
                    add_circle(c[0] - bottom.x0 + plan.x0, plan.y1 - (c[1] - bottom.y0), c[2], "bottom")
        for p in bottom.prims:
            if p.kind == "circle":
                # bottom view = plan mirrored top-to-bottom
                add_circle(p.cx - bottom.x0 + plan.x0, plan.y1 - (p.cy - bottom.y0), p.r, "bottom")

    holes = []
    for g in groups:
        rs = sorted(set((round(r, 4), s) for r, s in g["r"]))
        steps = []
        # merge radii seen from both sides
        radii = sorted(set(r for r, _ in rs))
        rmin = radii[0]
        CH = 0.07 / scale        # a ring this close to the drill with no depth info = edge chamfer
        for r in radii:
            sides = {s for rr, s in rs if rr == r}
            zr = None
            for rdr in readers:
                if not rdr.horizontal:
                    u1, u2 = g["y"] - r, g["y"] + r
                else:
                    u1, u2 = g["x"] - r, g["x"] + r
                a, b = rdr.z_range(u1), rdr.z_range(u2)
                if a and b:
                    zr = (max(a[0], b[0]), min(a[1], b[1])); break
            R = r * scale
            if r == rmin and (zr is None or (zr[0] < 0.01 and zr[1] > T - 0.01)):
                steps.append((R, "thru", T))
            elif zr is not None and zr[1] > T - 0.01 and zr[0] > 0.01:
                steps.append((R, "top", T - zr[0]))
            elif zr is not None and zr[0] < 0.01 and zr[1] < T - 0.01:
                steps.append((R, "bottom", zr[1]))
            elif zr is not None:
                steps.append((R, "thru", T))
            elif r - rmin < CH:
                continue
            else:
                side_ = "bottom" if sides == {"bottom"} else "top"
                steps.append((R, side_, None))
                notes.append(f"hole at ({(g['x']-ox)*scale:.3f}, {(g['y']-oy)*scale:.3f}) R{R:.4f}: depth not found in the side view")
        # a drill that stops short of the far face with nothing else seen: look in the side view for a
        # wider bore coming from the far face (a counterbore hidden in the plan view)
        if steps and len(steps) == 1 and steps[0][1] in ("top", "bottom") and steps[0][2] and steps[0][2] < T - 0.01:
            r0, s0, d0 = steps[0]
            z_stop = T - d0 if s0 == "top" else d0               # where the drill ends
            for rdr in readers:
                u0 = g["x"] if rdr.horizontal else g["y"]
                best = None
                for k in range(1, 60):
                    rr = r0 / scale + k * 0.002
                    a, b = rdr.z_range(u0 - rr, tol=0.0015), rdr.z_range(u0 + rr, tol=0.0015)
                    if a and b:
                        lo, hi = max(a[0], b[0]), min(a[1], b[1])
                        if s0 == "top" and lo < 0.01 and abs(hi - z_stop) < 0.01:
                            best = (rr * scale, "bottom", hi); break
                        if s0 == "bottom" and hi > T - 0.01 and abs(lo - z_stop) < 0.01:
                            best = (rr * scale, "top", T - lo); break
                if best:
                    # exact radius from the side-view lines found
                    rr = best[0] / scale
                    us = [u for u, a, b in rdr.lines if min(abs(u - (u0 - rr)), abs(u - (u0 + rr))) < 0.003]
                    if us:
                        rr = sum(abs(u - u0) for u in us) / len(us)
                    steps = [(r0, "thru", T), (round(rr * scale / 0.0005) * 0.0005, best[1], best[2])]
                    break
        # a drill seen from one face that meets a counterbore from the other face goes right through
        if steps:
            r0, s0, d0 = min(steps)
            for R, sd, d in steps:
                if R > r0 and s0 in ("top", "bottom") and sd in ("top", "bottom") and sd != s0 and d and d0 and d + d0 >= T - 0.01:
                    steps[steps.index(min(steps))] = (r0, "thru", T)
                    break
        holes.append(Hole(*M((g["x"], g["y"])), steps))

    # inner boundaries that are not circles: cut-outs / pockets
    cuts = []
    for ring in body.interiors:
        poly = Polygon(ring)
        c = circle_of_ring(ring, 0.0015)
        if c and any(math.dist((g["x"], g["y"]), c[:2]) < 0.003 for g in groups):
            continue
        if Polygon(ring).area < 1e-5:
            continue
        segs = [(k, *[M(q) for q in pts]) for k, *pts in to_segments(list(ring.coords), circles, ctol)]
        x0, y0, x1, y1 = poly.bounds
        side_, d = _cut_depth(readers, lambda r: (y0, y1) if not r.horizontal else (x0, x1), "top", T)
        cuts.append(Cut(segs, side_, d))
    if bottom is not None:
        fb = faces([p for p in bottom.prims])
        if fb:
            bbody = _material_face(fb)
            mir = lambda q: M((q[0] - bottom.x0 + plan.x0, plan.y1 - (q[1] - bottom.y0)))
            bcircles = [(p.cx, p.cy, p.r) for p in bottom.prims if p.kind in ("circle", "arc")]
            for ring in bbody.interiors:
                c = circle_of_ring(ring, 0.0015)
                if c or Polygon(ring).area < 1e-5:
                    continue
                segs = [(k, *[mir(q) for q in pts]) for k, *pts in to_segments(list(ring.coords), bcircles, ctol)]
                # mirrored ring runs the other way; depth from the side views at the mirrored extent
                poly = Polygon([mir(q) for q in ring.coords])
                x0, y0, x1, y1 = poly.bounds
                bx0, by0, bx1, by1 = x0 / scale + ox, y0 / scale + oy, x1 / scale + ox, y1 / scale + oy
                side_, d = _cut_depth(readers, lambda r: (by0, by1) if not r.horizontal else (bx0, bx1), "bottom", T)
                cuts.append(Cut(segs, side_, d))
    pm = PartModel(outline, T, holes, cuts, notes)
    _steps(pm, plan, readers, fs, body, outer, scale, M, circles, ctol)
    _side_holes(pm, plan, readers, outer, scale, M)
    _hidden_edge_holes(pm, plan, outer, scale, M)
    return pm


def _hidden_edge_holes(pm, plan, outer, scale, M):
    """holes drilled into an edge that no view looks at: in the plan they are two parallel hidden lines
    running in from the outline, one hole diameter apart. Placed at mid-thickness."""
    import numpy as np
    T = pm.thickness
    coords = list(outer.exterior.simplify(2e-4).coords)          # one segment per straight edge
    thin = [p for p in plan.thin if p.kind == "line"]
    found = 0
    for (ax, ay), (bx, by) in zip(coords, coords[1:]):
        L = math.dist((ax, ay), (bx, by))
        if L < 0.08:
            continue
        tx, ty = (bx - ax) / L, (by - ay) / L
        nx, ny = ty, -tx                                    # one of the normals; pick the one pointing inside
        mid = (ax + tx * L / 2 + nx * 0.01, ay + ty * L / 2 + ny * 0.01)
        if not outer.contains(Point(mid)):
            nx, ny = -nx, -ny
        spans = []
        for p in thin:
            (px, py), (qx, qy) = p.pts[0], p.pts[-1]
            dx, dy = qx - px, qy - py
            ln = math.hypot(dx, dy)
            if ln < 1e-4 or abs(dx * tx + dy * ty) / ln > 0.03:     # must run perpendicular to the edge
                continue
            tau = (px - ax) * tx + (py - ay) * ty
            if tau < 0.01 or tau > L - 0.01:
                continue
            n0 = (px - ax) * nx + (py - ay) * ny; n1 = (qx - ax) * nx + (qy - ay) * ny
            spans.append((tau, min(n0, n1), max(n0, n1)))
        spans.sort(key=lambda s: (round(s[0] / 0.002), s[1]))
        merged = []
        for tau, a, b in spans:
            if merged and abs(merged[-1][0] - tau) < 0.002 and a <= merged[-1][2] + 0.07:
                merged[-1] = (merged[-1][0], min(merged[-1][1], a), max(merged[-1][2], b))
            else:
                merged.append((tau, a, b))
        runs = [m for m in merged if -0.005 < m[1] < 0.03 and m[2] - m[1] > 0.04]
        cand = []
        for i in range(len(runs)):
            for j in range(i + 1, len(runs)):
                (t1, a1, b1), (t2, a2, b2) = runs[i], runs[j]
                sp = t2 - t1
                if sp * scale > 0.6 or sp * scale < 0.05 or abs(b1 - b2) > 0.01:
                    continue
                if any(t1 + 0.002 < t < t2 - 0.002 and abs(t - (t1 + t2) / 2) > 0.003 for t, a, b in runs):
                    continue                                   # other lines in between: not one hole (a centre line is fine)
                cand.append((sp, i, j))
        used = set()
        for sp, i, j in sorted(cand):                          # narrowest pairs first: a tapped hole's minor diameter
            if i in used or j in used:
                continue
            (t1, a1, b1), (t2, a2, b2) = runs[i], runs[j]
            used.update((i, j))
            # lines just outside this pair are the same hole drawn at its thread size
            used.update(k for k, (t, a, b) in enumerate(runs) if min(abs(t - t1), abs(t - t2)) < 0.012)
            tc = (t1 + t2) / 2
            ex, ey = M((ax + tx * tc, ay + ty * tc))
            depth = (b1 + b2) / 2 * scale
            dup = next((h for h in pm.side_holes if math.dist(h.entry, (ex, ey)) < 0.02), None)
            if dup is not None:
                if dup.depth < depth:
                    dup.depth = depth                          # the plan view shows the full depth
                continue
            pm.side_holes.append(SideHole((ex, ey), (nx, ny), T / 2, sp * scale / 2, depth))
            found += 1
    if found:
        pm.notes.append(f"{found} edge hole(s) found from hidden lines only: placed at mid-thickness, check the height")


def _zmodel(rdr, t):
    z = (t - rdr.t0) * rdr.scale
    return z if rdr.top_at_high else rdr.T - z


def _steps(pm, plan, readers, fs, body, outer, scale, M, circles, ctol):
    """stepped thickness: the plate is `base` thick and some plan faces rise higher. A raised face is one whose
    extent along the side view matches a flat (top edge) in that view higher than the base."""
    T = pm.thickness
    cands = []
    for rdr in readers:
        span = (rdr.v.x1 - rdr.v.x0) if rdr.horizontal else (rdr.v.y1 - rdr.v.y0)
        for t, a, b in rdr._merge(list(rdr.vflats), gap=0.01):
            z = _zmodel(rdr, t)
            if 0.05 < z < T - 0.06 and (b - a) > 0.5 * span:      # lines closer to a face are edge chamfers
                cands.append(z)
    if not cands:
        return
    base = round(max(cands) / 0.0005) * 0.0005
    raised = []
    for f in fs:
        if f.area < 0.03 * outer.area or f.area > 0.7 * outer.area:
            continue
        x0, y0, x1, y1 = f.bounds
        for rdr in readers:
            u0, u1 = (x0, x1) if rdr.horizontal else (y0, y1)
            for t, a, b in rdr._merge(list(rdr.vflats), gap=0.01):
                z = _zmodel(rdr, t)
                chamfer = 0.001 < T - z < 0.035            # edge-break line just under the top, not a level
                if z > base + 0.03 and not chamfer and abs(a - u0) < 0.004 and abs(b - u1) < 0.004:
                    raised.append((f, z)); break
            else:
                continue
            break
    if not raised:
        pm.notes.append(f"side view shows a step at z={base:.4f} but no plan area matches it - built {T:.4f} thick everywhere")
        return
    pm.base = base
    for f, z in raised:
        ring = [(k, *[M(q) for q in pts]) for k, *pts in to_segments(list(f.exterior.coords), circles, ctol)]
        inner = [[(k, *[M(q) for q in pts]) for k, *pts in to_segments(list(r.coords), circles, ctol)] for r in f.interiors]
        pm.raised.append(Raised(ring, inner, round(z / 0.0005) * 0.0005))
    pm.notes.append(f"stepped part: {base:.4f} thick, {len(raised)} raised area(s) up to {max(z for _, z in raised):.4f}")


def _side_holes(pm, plan, readers, outer, scale, M):
    """circles in a side view are holes drilled into that edge of the part; depth from the plan view lines"""
    T = pm.thickness
    for rdr in readers:
        circ = [p for p in rdr.v.prims if p.kind == "circle"]
        if not circ:
            continue
        for p in circ:
            u = p.cx if rdr.horizontal else p.cy
            t = p.cy if rdr.horizontal else p.cx
            z = _zmodel(rdr, t)
            # where the hole enters: the outline edge facing this view
            from shapely.geometry import LineString as LS
            if rdr.horizontal:
                ray = LS([(u, plan.y0 - 1), (u, plan.y1 + 1)])
            else:
                ray = LS([(plan.x0 - 1, u), (plan.x1 + 1, u)])
            hit = outer.exterior.intersection(ray)
            pts = [hit] if hit.geom_type == "Point" else list(getattr(hit, "geoms", []))
            if not pts:
                continue
            key = {"above": lambda q: -q.y, "below": lambda q: q.y, "right": lambda q: -q.x, "left": lambda q: q.x}[rdr.where]
            e = min(pts, key=key)
            d = {"above": (0, -1), "below": (0, 1), "right": (-1, 0), "left": (1, 0)}[rdr.where]
            # depth: plan-view lines along the hole axis at u +/- r, starting at the entry
            depth = None
            r = p.r
            for q in plan.thin + plan.prims:
                if q.kind != "line":
                    continue
                (ax, ay), (bx, by) = q.pts[0], q.pts[-1]
                if rdr.horizontal and abs(ax - bx) < 1e-3 and min(abs(ax - (u - r)), abs(ax - (u + r))) < 0.003:
                    far = max(abs(ay - e.y), abs(by - e.y))
                    near = min(abs(ay - e.y), abs(by - e.y))
                    if near < 0.03:
                        depth = max(depth or 0, far)
                if not rdr.horizontal and abs(ay - by) < 1e-3 and min(abs(ay - (u - r)), abs(ay - (u + r))) < 0.003:
                    far = max(abs(ax - e.x), abs(bx - e.x)); near = min(abs(ax - e.x), abs(bx - e.x))
                    if near < 0.03:
                        depth = max(depth or 0, far)
            if depth is None:
                pm.notes.append(f"side hole R{r * scale:.4f} at z={z:.3f}: depth not found, made 2 x diameter")
                depth = 4 * r
            ex, ey = M((e.x, e.y))
            pm.side_holes.append(SideHole((ex, ey), d, z, r * scale, depth * scale))
