"""Run a PyOps operation from a script or console, without touching the Add
Operation panel.

The panel builds a new operation instance through three linked Scene
properties (`ui_parent`, `ui_category_dropdown`, `ui_op_dropdown`) plus one
entry in `ui_factory_props` — see callbacks.py and engine.py. Driving that by
hand from a Blender-MCP session means reverse-engineering the cascade every
time; this wraps it in one call.

    import refops_api
    refops_api.scripting.run_operation(
        parent_obj, "BIM_WALL_GENERATOR",
        bool_attr="sel_wall", wall_type_pointer=wall_type_obj,
    )
"""

import bpy

from . import registry
from .registry import OPERATIONS


def run_operation(parent_obj, op_id, **field_values):
    """Create a new operation instance on `parent_obj` and run it once.

    field_values are set on the operation's PropertyGroup by name — the same
    fields props_definition declares, the same as the panel would set. Returns
    the new container object (the engine's duplicate the operation actually
    wrote into), or None if it could not be created.
    """
    op_cls = OPERATIONS.get(op_id)
    if op_cls is None:
        print(f"[{__package__}] Operação desconhecida: '{op_id}'")
        return None
    category_tag = getattr(op_cls, "category_tag", None)
    if not category_tag:
        print(f"[{__package__}] '{op_id}' sem category_tag — addon carregado errado?")
        return None

    scn = bpy.context.scene
    scn.ui_parent = parent_obj
    try:
        # These two are dynamic enums filtered by parent_obj.type (see
        # callbacks.get_categories_callback / get_operations_callback) — an
        # op_id that doesn't accept this object type raises here, not later.
        scn.ui_category_dropdown = category_tag
        scn.ui_op_dropdown = op_id
    except TypeError as e:
        print(
            f"[{__package__}] '{op_id}' não aceita objetos do tipo "
            f"'{parent_obj.type}' (categoria '{category_tag}'): {e}"
        )
        return None

    if not scn.ui_factory_props:
        print(f"[{__package__}] '{op_id}' não montou propriedades — cascata de dropdowns falhou.")
        return None

    holder = getattr(scn.ui_factory_props[0], registry.storage_attr(op_id))
    for name, value in field_values.items():
        try:
            setattr(holder, name, value)
        except (AttributeError, TypeError) as e:
            print(f"[{__package__}] '{op_id}.{name}' = {value!r}: {e}")

    result = bpy.ops.mesh.procedural_engine(is_refresh=False)
    if "FINISHED" not in result:
        print(f"[{__package__}] '{op_id}' -> {result}")
        return None
    return bpy.context.view_layer.objects.active
