# 2dto3d (pdf2step)

Builds 3D STEP models of **plate-type parts** (flat plates, blocks, brackets with holes and pockets) from a
**vector PDF** drawing exported from CAD (NX, SolidWorks, Creo, Inventor, Fusion ...).

```
python pdf2step.py drawing.pdf --page 13 -o out/                # scale read from the title block
python pdf2step.py drawing.pdf --page 13 --scale 3 -o out/      # or give it (3 = drawing scale 1:3)
```

`pip install -r requirements.txt` installs everything, including the OCR used to read the sheet's text
(RapidOCR, which runs locally). If RapidOCR is not installed, Tesseract is used when it is on the PATH; with neither,
the tool works from the geometry only and `--scale` is required.

## How it works
1. **Reads the lines, not a picture.** The PDF keeps the drawing as true lines, arcs and circles. Thick
   strokes are visible edges; thin strokes are hidden / centre / dimension lines; filled shapes (text, arrow
   heads) are ignored. Sizes come straight from the geometry and the sheet scale, so they are exact (snapped to 0.0005).
2. **Finds the views**: connected groups of visible edges; the sheet border, title block, tables and isometric
   views are dropped. Views drawn turned on the sheet (e.g. a part lying at 33 deg) are straightened first.
3. **Pairs views** (third-angle): the plan view, the thin side / section views lined up with it, and a bottom
   view (a mirrored twin directly below) if there is one.
4. **Recognises features**
   - outline: outer boundary of the plan view (arcs rebuilt as true arcs; edge-break double lines skipped)
   - thickness: from the side view
   - holes: concentric circles from the top view (and the bottom view, mirrored); each step's depth and side
     from the lines at its edges in the side / section views (drill, counterbore, blind hole)
   - cut-outs and pockets: inner boundaries; depth from the pocket floor line in the side / section view
   - counterbores on the far face: dashed circles in the plan, or a drill that stops short in the side view
   - stepped thickness: a plan area whose width matches a higher top edge in the side view is raised to it
   - holes in the edges: circles in a side view (depth from the plan), or two hidden lines running in from an
     edge one hole-diameter apart (placed at mid-thickness, flagged in the report)
5. **Reads the text** (OCR on the lettering, which CAD exports usually draw as outlines): the title block scale and
   the hole notes. A note is matched to its hole by following its leader line; "6X #10-24 UNC ↧.50", "26X Ø.21 THRU",
   "⌴Ø.38 ↧.205" set the drill size (tap drill for threads), depth, through or counterbore of every hole of that
   kind. Threads found are listed in the report.
6. **Builds** the solid with CadQuery and exports STEP.

## Limits
- Threads are built at the drawn hole size; slanted features are not built.
- Stepped parts: only raised areas whose edges show in a side view; pockets inside raised areas are not handled.
- Parts that need sections to be understood (rings, lips, undercuts) are listed as "unused views".
- Detail views at other scales are ignored, and so are notes that only point into a detail view.

`compare.py auto.step reference.step` reports the volume overlap between an auto model and a reference.
