"""Backing collections for search-enabled String fields.

A String prop opts in with {"search": "<source>"} in its props_definition args.
The prop stays a plain StringProperty and the panel draws it with
layout.prop_search(), which gives Blender's data-block widget: a text field with
a search icon and an X to clear. (It used to be converted to an EnumProperty and
drawn as a dropdown — that can't be cleared and doesn't accept free text.)

prop_search needs a real RNA collection to search against. Attributes, vertex
groups and materials all have one; custom properties don't, so a mirror
collection of names is kept on the scene and synced before drawing.
"""

import bpy

from .registry import INTERNAL_KEYS


class REFOPS_PG_search_name(bpy.types.PropertyGroup):
    """Just the implicit `name` — a row in a mirror collection for prop_search."""


def resolve_search_parent(data_holder, context):
    """Resolves the object whose data should back a search-enabled field.
    Prioritizes id_data — the actual owner of the property being drawn — over
    context.scene.ui_parent, which reflects whatever object was last picked in
    the 'Add Operation' section and can be stale/unrelated when the field being
    drawn actually belongs to an *existing* copy's Active Properties."""
    owner = getattr(data_holder, "id_data", None)
    if isinstance(owner, bpy.types.Object) and owner.is_procedural_copy:
        obj = bpy.data.objects.get(owner.proc_parent_name)
        if obj:
            return obj
    elif isinstance(owner, bpy.types.Scene):
        obj = getattr(owner, "ui_parent", None)
        if obj:
            return obj

    if context is not None:
        scn = getattr(context, "scene", None)
        if scn is not None and getattr(scn, "ui_parent", None):
            return scn.ui_parent
        active_obj = getattr(context, "active_object", None)
        if isinstance(active_obj, bpy.types.Object) and active_obj.is_procedural_copy:
            return bpy.data.objects.get(active_obj.proc_parent_name)

    return None


def _show_hidden():
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        return prefs.show_hidden_attributes
    except Exception:
        return True


def custom_property_names(obj):
    if not obj:
        return []
    return [
        k for k in obj.keys() if not k.startswith("_") and k not in INTERNAL_KEYS
    ]


def sync_name_collection(collection, names):
    """Rewrites the mirror collection only when the contents actually changed —
    an unconditional clear/refill during draw would retrigger redraws forever."""
    names = list(names)
    if [item.name for item in collection] == names:
        return
    collection.clear()
    for name in names:
        collection.add().name = name


def resolve_search_target(source, parent, context):
    """Returns (data, collection_prop_name, icon) for layout.prop_search, or None
    when the source can't be resolved right now (no parent picked, no mesh, ...)."""
    if source == "attribute":
        if not parent or not getattr(parent, "data", None):
            return None
        if not hasattr(parent.data, "attributes"):
            return None
        if _show_hidden():
            return parent.data, "attributes", "MESH_DATA"
        # prop_search lists whatever collection it is given, so hiding the
        # dot-prefixed internals means pointing it at a mirror. The mirror is
        # filled by refresh_mirrors from a depsgraph handler, never from here:
        # Blender forbids writing to an ID during draw, and doing it here threw
        # on every redraw and left the whole panel blank.
        scn = getattr(context, "scene", None)
        keys = getattr(scn, "ui_search_attributes", None) if scn is not None else None
        if keys is None:
            return parent.data, "attributes", "MESH_DATA"
        return scn, "ui_search_attributes", "MESH_DATA"

    if source == "vertex_group":
        if parent and hasattr(parent, "vertex_groups"):
            return parent, "vertex_groups", "GROUP_VERTEX"
        return None

    if source == "material":
        return bpy.data, "materials", "MATERIAL"

    if source == "text":
        # Blender's own text editor as a data source — no file path needed, and
        # the data travels inside the .blend.
        return bpy.data, "texts", "TEXT"

    if source == "custom_property":
        # Same rule: the mirror is filled by the handler, read here.
        scn = getattr(context, "scene", None)
        keys = getattr(scn, "ui_search_keys", None) if scn is not None else None
        if keys is None:
            return None
        return scn, "ui_search_keys", "RNA"

    return None


def refresh_mirrors(context):
    """Fill the mirror collections the search fields read.

    Called from the depsgraph handler, which is a context where writing to the
    scene is allowed — draw() is not. Both mirrors are rebuilt from whichever
    object the search fields would resolve to, so switching parent objects
    updates the lists on the next depsgraph tick."""
    scn = getattr(context, "scene", None)
    if scn is None:
        return

    parent = getattr(scn, "ui_parent", None)
    if parent is None:
        active = getattr(context, "active_object", None)
        if active is not None and getattr(active, "is_procedural_copy", False):
            parent = bpy.data.objects.get(active.proc_parent_name)

    attributes = []
    if parent is not None and getattr(parent, "data", None) is not None:
        source = getattr(parent.data, "attributes", None)
        if source is not None:
            attributes = [a.name for a in source if not a.name.startswith(".")]

    keys = getattr(scn, "ui_search_attributes", None)
    if keys is not None:
        sync_name_collection(keys, attributes)

    properties = getattr(scn, "ui_search_keys", None)
    if properties is not None:
        sync_name_collection(properties, custom_property_names(parent))


SEARCH_SOURCES = {"attribute", "vertex_group", "material", "custom_property", "text"}

classes = (REFOPS_PG_search_name,)
