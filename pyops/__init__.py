bl_info = {
    "name": "PyOps",
    "author": "Arthur Vilarinho",
    "version": (1, 7, 3),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Procedural Tab",
    "description": "Open architecture add-on that scans external python scripts to run reference-driven, non-destructive model updates",
    "category": "Mesh",
}

import importlib
import sys

# Blender keeps submodules in sys.modules across "Reload Scripts", so without an
# explicit reload the package re-registers against stale module objects.
_SUBMODULES = (
    "registry",
    "animation",
    "dependencies",
    "profiling",
    "search",
    "properties",
    "loader",
    "callbacks",
    "snapshot",
    "engine",
    "attributes",
    "ui",
    "bim_helpers",
    "bim_profiles",
    "ladybug_helpers",
    "honeybee_helpers",
    "scripting",
)

if "bpy" in locals():
    for _name in _SUBMODULES:
        _mod = sys.modules.get(f"{__package__}.{_name}")
        if _mod is not None:
            importlib.reload(_mod)

import bpy

from . import (
    animation,
    attributes,
    dependencies,
    bim_helpers,
    bim_profiles,
    callbacks,
    engine,
    honeybee_helpers,
    ladybug_helpers,
    loader,
    properties,
    scripting,
    search,
    ui,
)

# Handle to this package for operator files, which are exec'd from a user-chosen
# folder and can't use a relative import. The loader pre-binds it as a global in
# each operator file (`pyops_api.bim_helpers`); consoles and MCP scripts read it
# from bpy.app.driver_namespace. Not sys.modules: Blender Extensions reject any
# top-level module the add-on registers there ("Policy violation with top level
# module"), and the package name itself varies with where it was installed.
API_ALIAS = "pyops_api"


def _all_classes():
    # search first: its PropertyGroup is referenced by a Scene property below.
    return (
        search.classes
        + attributes.classes
        + engine.classes
        + dependencies.classes
        + profiling.classes
        + ui.classes
    )


def register():
    bpy.app.driver_namespace[API_ALIAS] = sys.modules[__name__]

    for cls in _all_classes():
        bpy.utils.register_class(cls)

    bpy.types.Scene.ui_parent = bpy.props.PointerProperty(
        name="Parent Object",
        type=bpy.types.Object,
        update=callbacks.update_parent_dropdown,
    )
    bpy.types.Scene.ui_category_dropdown = bpy.props.EnumProperty(
        name="Category",
        items=callbacks.get_categories_callback,
        update=callbacks.update_category_dropdown,
    )
    bpy.types.Scene.ui_op_dropdown = bpy.props.EnumProperty(
        name="Operation",
        items=callbacks.get_operations_callback,
        update=callbacks.update_op_dropdown,
    )
    bpy.types.Scene.ui_auto_refresh_enabled = bpy.props.BoolProperty(
        name="Auto-Refresh", default=False
    )
    bpy.types.Scene.ui_live_refresh_enabled = bpy.props.BoolProperty(
        name="Live Refresh on Edit", default=True
    )
    bpy.types.Scene.ui_show_refresh_order = bpy.props.BoolProperty(
        name="Refresh Order", default=False
    )
    bpy.types.Scene.ui_frame_refresh_enabled = bpy.props.BoolProperty(
        name="Refresh on Frame Change",
        description="Rebuild opted-in copies when the frame changes and the source geometry actually differs",
        default=False,
    )
    bpy.types.Scene.ui_attr_rows = bpy.props.CollectionProperty(
        type=attributes.PYOPS_PG_attribute_row
    )
    # Mirror collection so custom-property fields can use prop_search, which
    # needs a real RNA collection and custom properties don't have one.
    bpy.types.Scene.ui_search_keys = bpy.props.CollectionProperty(
        type=search.PYOPS_PG_search_name
    )
    # A second mirror, for attribute names with the internal ones filtered out.
    bpy.types.Scene.ui_search_attributes = bpy.props.CollectionProperty(
        type=search.PYOPS_PG_search_name
    )

    bpy.types.Object.is_procedural_copy = bpy.props.BoolProperty(default=False)
    bpy.types.Object.procedural_op_type = bpy.props.StringProperty(default="")
    bpy.types.Object.procedural_auto_update = bpy.props.BoolProperty(
        name="Auto Update", default=False
    )
    bpy.types.Object.proc_parent_name = bpy.props.StringProperty(
        name="Parent Object Name", default=""
    )
    bpy.types.Object.procedural_order = bpy.props.IntProperty(
        name="Refresh Order",
        description="Lower runs first among operations at the same chain depth",
        default=0,
    )

    # The external library folder must be on sys.path before any operation
    # tries to import Ladybug or Honeybee.
    dependencies.activate()

    loader.load_operations_from_disk()
    properties.register_data_storage()

    handler = attributes.selection_attribute_rows_update_handler
    if handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(handler)

    if animation.frame_change_handler not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(animation.frame_change_handler)
    for queue, callback in (
        (bpy.app.handlers.render_init, animation.render_start_handler),
        (bpy.app.handlers.render_complete, animation.render_end_handler),
        (bpy.app.handlers.render_cancel, animation.render_end_handler),
    ):
        if callback not in queue:
            queue.append(callback)

    attributes.refresh_selection_attribute_rows(bpy.context)


def unregister():
    for handler in (
        attributes.selection_attribute_rows_update_handler,
        engine.procedural_live_depsgraph_handler,
    ):
        if handler in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(handler)

    for queue, callback in (
        (bpy.app.handlers.frame_change_post, animation.frame_change_handler),
        (bpy.app.handlers.render_init, animation.render_start_handler),
        (bpy.app.handlers.render_complete, animation.render_end_handler),
        (bpy.app.handlers.render_cancel, animation.render_end_handler),
    ):
        if callback in queue:
            queue.remove(callback)
    animation.forget_fingerprints()

    properties.unregister_data_storage()

    for owner, prop in (
        (bpy.types.Scene, "ui_parent"),
        (bpy.types.Scene, "ui_category_dropdown"),
        (bpy.types.Scene, "ui_op_dropdown"),
        (bpy.types.Scene, "ui_auto_refresh_enabled"),
        (bpy.types.Scene, "ui_live_refresh_enabled"),
        (bpy.types.Scene, "ui_frame_refresh_enabled"),
        (bpy.types.Scene, "ui_show_refresh_order"),
        (bpy.types.Scene, "ui_attr_rows"),
        (bpy.types.Scene, "ui_search_keys"),
        (bpy.types.Scene, "ui_search_attributes"),
        (bpy.types.Object, "is_procedural_copy"),
        (bpy.types.Object, "procedural_op_type"),
        (bpy.types.Object, "procedural_auto_update"),
        (bpy.types.Object, "proc_parent_name"),
        (bpy.types.Object, "procedural_order"),
    ):
        if hasattr(owner, prop):
            delattr(owner, prop)

    for cls in reversed(_all_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass

    bpy.app.driver_namespace.pop(API_ALIAS, None)


if __name__ == "__main__":
    register()
