# pdf2step

Builds 3D STEP models of **plate-type parts** (flat plates, blocks, brackets with holes and pockets) from a
**vector PDF** drawing exported from CAD (NX, SolidWorks, Creo, Inventor, Fusion ...).

```
python pdf2step.py drawing.pdf --page 13 --scale 3 -o out/      # scale 3 = drawing scale 1:3
```
One STEP file per part found on the page (in mm), plus `pNN_report.json` with what was found and anything to check.

## How it works
1. **Reads the lines, not a picture.** The PDF keeps the drawing as true lines, arcs and circles. Thick
   strokes are visible edges; thin strokes are hidden / centre / dimension lines; filled shapes (text, arrow
   heads) are ignored. Sizes come straight from the geometry and the sheet scale, so they are exact (snapped to 0.0005).
2. **Finds the views**: connected groups of visible edges; the sheet border, title block, tables and isometric
   views are dropped.
3. **Pairs views** (third-angle): the plan view, the thin side / section views lined up with it, and a bottom
   view (a mirrored twin directly below) if there is one.
4. **Recognises features**
   - outline: outer boundary of the plan view (arcs rebuilt as true arcs; edge-break double lines skipped)
   - thickness: from the side view
   - holes: concentric circles from the top view (and the bottom view, mirrored); each step's depth and side
     from the lines at its edges in the side / section views (drill, counterbore, blind hole)
   - cut-outs and pockets: inner boundaries; depth from the pocket floor line in the side / section view
5. **Builds** the solid with CadQuery and exports STEP.

## Limits (v1)
- One thickness per part: a part with raised areas or steps is built at full height (flagged in the report).
- Holes drilled into side faces, threads (built at the drawn size), slanted features, and parts whose plan view is
  rotated on the sheet are not built.
- Parts that need sections to be understood (rings, lips, undercuts) are listed as "unused views".
- The drawing scale must be given (`--scale`); detail views at other scales are ignored.

`compare.py auto.step reference.step` reports the volume overlap between an auto model and a reference.
