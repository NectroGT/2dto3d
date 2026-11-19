"""Attach hole notes (callouts) to the holes they point at, and apply them."""
import math
from text import TAP


def leader_tips(note, thin_lines, reach=0.08, join=0.012):
    """follow the leader line(s) that start at a note's text box; return the far end points"""
    x0, y0, x1, y1 = note["x0"] - reach, note["y0"] - reach, note["x1"] + reach, note["y1"] + reach
    segs = [(p.pts[0], p.pts[-1]) for p in thin_lines if math.dist(p.pts[0], p.pts[-1]) > 0.02]
    inbox = lambda q: x0 <= q[0] <= x1 and y0 <= q[1] <= y1
    tips = []
    for a, b in segs:
        for s_, e_ in ((a, b), (b, a)):
            if not inbox(s_) or inbox(e_):
                continue
            cur, seen = e_, {(a, b)}
            for _ in range(4):                 # a leader may bend once or twice
                nxt = next(((c, d) for c, d in segs if (c, d) not in seen and
                            (math.dist(c, cur) < join or math.dist(d, cur) < join)), None)
                if nxt is None:
                    break
                seen.add(nxt)
                cur = nxt[1] if math.dist(nxt[0], cur) < join else nxt[0]
            tips.append(cur)
    return tips


def attach(notes, thin_lines, hole_groups, side_holes_paper):
    """hole_groups: [dict(x, y, r=[(radius, side)]) in paper units]; side_holes_paper: [(index, (x, y))].
    Returns {("hole", i) | ("side", i): info}"""
    out = {}
    for n in notes:
        for tip in leader_tips(n, thin_lines):
            best = None
            for i, g in enumerate(hole_groups):
                for r, _ in g["r"]:
                    d = abs(math.dist(tip, (g["x"], g["y"])) - r)
                    if math.dist(tip, (g["x"], g["y"])) <= r + 0.03 and (best is None or d < best[0]):
                        best = (d, ("hole", i))
            for i, q in side_holes_paper:
                d = math.dist(tip, q)
                if d < 0.12 and (best is None or d < best[0]):
                    best = (d, ("side", i))
            if best:
                out[best[1]] = n["info"]
    return out


def apply_hole(h, info, T, notes, label):
    """change a Hole's steps to follow its note"""
    steps = sorted(h.steps)
    if not steps:
        return
    r0, side0, d0 = steps[0]
    tag = []
    if "thread" in info and info["thread"] in TAP:
        r0 = TAP[info["thread"]] / 2
        tag.append(info["thread"])
    elif "dia" in info and abs(info["dia"] / 2 - r0) < 0.15 * r0:
        r0 = info["dia"] / 2
    if info.get("thru"):
        side0, d0 = "thru", T
    elif "depth" in info and info["depth"] < T:
        if side0 == "thru":
            side0 = "top"
        d0 = info["depth"]
    steps[0] = (r0, side0, d0)
    if "cbore_d" in info and len(steps) > 1:
        R, sd, d = steps[1]
        if abs(info["cbore_d"] / 2 - R) < 0.2 * R:
            steps[1] = (info["cbore_d"] / 2, sd, info.get("cbore_depth", d))
    h.steps = steps
    h.note = " ".join(tag) or None
