"""Scans the user's scripts directory and registers operation classes.

Files are exec'd, not imported — each gets a fresh namespace pre-seeded with
`pyops_api` (this package) so operator files reach shared helpers as
`pyops_api.bim_helpers` without an import, regardless of where the add-on is
installed.
"""

import os
import sys

import bpy

from .registry import OPERATIONS


def load_operations_from_disk():
    OPERATIONS.clear()
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        root_folder = bpy.path.abspath(prefs.scripts_directory)
    except Exception:
        return

    if not root_folder or not os.path.exists(root_folder):
        return

    for root, dirs, files in os.walk(root_folder):
        for filename in files:
            if filename.endswith(".py") and filename != "__init__.py":
                try:
                    path = os.path.join(root, filename)
                    with open(path, "r", encoding="utf-8") as f:
                        code = f.read()

                    module_namespace = {
                        "__file__": path,
                        "__name__": filename[:-3],
                        "pyops_api": sys.modules[__package__],
                    }
                    exec(compile(code, path, "exec"), module_namespace)

                    for attr in module_namespace.values():
                        if (
                            isinstance(attr, type)
                            and hasattr(attr, "id")
                            and hasattr(attr, "props_definition")
                        ):
                            if isinstance(attr.id, str) and isinstance(
                                attr.props_definition, dict
                            ):
                                subfolder_name = os.path.basename(root)
                                attr.category_tag = subfolder_name.upper()
                                OPERATIONS[attr.id] = attr
                except Exception as e:
                    print(f"[{__package__}] Failed to parse file {filename}: {e}")
