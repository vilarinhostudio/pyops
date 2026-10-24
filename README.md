# PyOps

A Blender add-on that generates real IFC elements (walls, slabs, columns, beams, panels, openings, spaces) from attributes painted on a reference mesh, using Bonsai BIM and IfcOpenShell. Every `.py` script in a folder becomes an operation in the interface, and the workflow is non-destructive: the parent mesh is duplicated and the operation runs on the copy. The same attribute mechanism drives climate and daylight analysis with Ladybug, Honeybee and Radiance.

## Links

- **Install the add-on (auto-updating):** in Blender 4.2+, go to *Preferences → Get Extensions → Repositories → + → Add Remote Repository* and paste `https://vilarinhostudio.github.io/pyops/index.json`. Then install **PyOps** from the list.
- **Install the add-on (manual):** [pyops.zip](https://github.com/vilarinhostudio/pyops/releases/latest/download/pyops.zip), via *Get Extensions → Install from Disk*.
- **Operations (`ops/`):** [ops.zip](https://github.com/vilarinhostudio/pyops/releases/latest/download/ops.zip). Unzip it and point *Root Directory*, in the add-on preferences, at the `ops/` folder.
- **Drawing styles (`drawing-bonsai/`):** [drawing-bonsai.zip](https://github.com/vilarinhostudio/pyops/releases/latest/download/drawing-bonsai.zip), with SVG/CSS hatches and themes for Bonsai plans and sections.
- **What's new in each version:** [Releases](https://github.com/vilarinhostudio/pyops/releases) and [CHANGELOG.md](CHANGELOG.md).
- **Documentation:** [docs/](docs/).
