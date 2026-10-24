# Changelog

Each version becomes a GitHub Release whose notes are its section below. To publish: bump `version` in `pyops/blender_manifest.toml` (and `bl_info`), write the section here, commit, and push a `vX.Y.Z` tag.

## [1.7.3] - 2026-10-24

### Fixed
- Changing the parent object or the Category left a stale selection behind: the Operation dropdown showed blank instead of "Select an Operation...". Both dropdowns now return to their placeholder.

### Changed
- README, changelog and extension metadata are now in English.

## [1.7.2] - 2026-10-24

### Fixed
- The Category and Operation dropdowns flooded the Blender log with `current value '0' matches no enum` on every redraw, which could freeze Blender. The placeholder item now has the identifier `NONE` instead of an empty one.

## [1.7.1] - 2026-10-24

### Fixed
- Installing as an extension reported "Policy violation with top level module": the add-on registered a top-level module in `sys.modules`. Files in `ops/` now get `pyops_api` directly in their namespace; scripts and MCP sessions use `bpy.app.driver_namespace["pyops_api"]`.

### Changed
- The `refops` prefix became `pyops` everywhere (classes, operators, logs, attributes). The default libraries folder is now `C:\pyops_libs`: rename the old one or reinstall. Attributes with the old name in existing `.blend`/`.ifc` files no longer match.
- Operator files in `ops/` no longer `import pyops_api`.

## [1.7.0] - 2026-10-24

First version published as a Blender Extension.

### Added
- Distribute: `IfcAnnotation` output for drawings (Bonsai).
- EnergyPlus operator (Honeybee) and improvements to Radiance and `type_geometry`.
- `blender_manifest.toml`: install and update through the extensions repository.

### Changed
- Improved `panel` operator.
- Documentation and context reorganized (`.claude/`, `docs/`).
