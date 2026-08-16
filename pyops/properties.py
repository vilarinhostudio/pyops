"""Per-operation PropertyGroup construction and registration.

One PropertyGroup per operation, plus a container PropertyGroup holding a
PointerProperty to each. Flattening every operation's props into a single group
would make two operations that declare the same prop name collide — the second
definition is silently dropped and the first one's type wins.
"""

import traceback

import bpy

from . import registry
from .registry import (
    OPERATIONS,
    PROP_ALIASES,
    PROP_TYPE_MAP,
    UI_ONLY_TYPES,
    storage_attr,
)
from .search import SEARCH_SOURCES

_data_storage_cls = None
_op_storage_classes = {}


def _on_property_changed(self, context):
    """Fired whenever any procedural property is edited in the N-panel. Schedules a
    refresh of the owning object on the next event-loop tick (bpy.app.timers), rather
    than calling the operator synchronously from inside the RNA update — doing it
    synchronously runs mode_set/duplicate mid-interaction (e.g. while a slider is being
    dragged) and can silently fail, which is what made the bevel appear to vanish.
    Skips scheduling while a refresh is already in progress (re-entrant writeback of
    saved_values) or already queued for this object (debounces fast successive edits)."""
    if registry.refresh_lock:
        return
    scn = getattr(context, "scene", None)
    if scn is not None and not scn.ui_live_refresh_enabled:
        return  # kill-switch: lets us confirm whether this feature is the culprit
    owner = self.id_data
    if not (isinstance(owner, bpy.types.Object) and owner.is_procedural_copy):
        return

    name = owner.name
    if name in registry.pending_refresh:
        return
    registry.pending_refresh.add(name)

    def _deferred_refresh():
        registry.pending_refresh.discard(name)
        obj = bpy.data.objects.get(name)
        if obj and obj.is_procedural_copy:
            try:
                registry.make_active(obj)
                result = bpy.ops.mesh.procedural_engine(is_refresh=True)
                print(f"[{__package__}] Live refresh of '{name}' -> {result}")
            except Exception:
                # Surface the real error instead of the object just silently reverting.
                print(f"[{__package__}] Live refresh failed for '{name}':")
                traceback.print_exc()
        return None  # run once, don't repeat

    bpy.app.timers.register(_deferred_refresh, first_interval=0.0)


def resolve_alias(prop_type, prop_args):
    """Expand a named shortcut ("Distance", "Angle", "Vector4", ...) into its
    base type plus preset kwargs. Explicit args always win over the preset."""
    base_type, preset = PROP_ALIASES.get(prop_type, (prop_type, {}))
    merged = dict(preset)
    merged.update(prop_args)
    return base_type, merged


def _build_prop(prop_type, prop_args):
    prop_type, args = resolve_alias(prop_type, prop_args)
    args.pop("show_if", None)
    search_source = args.pop("search", None)
    if args.pop("attribute_search", False) and not search_source:
        search_source = "attribute"

    if prop_type == "Material":
        args["type"] = bpy.types.Material
    elif prop_type == "Object":
        args["type"] = bpy.types.Object
    elif prop_type == "Collection":
        args["type"] = bpy.types.Collection
    elif prop_type == "Path":
        path_kind = args.pop("path_type", "FILE")
        args["subtype"] = "DIR_PATH" if path_kind == "DIR" else "FILE_PATH"
    elif prop_type == "Color":
        args.setdefault("subtype", "COLOR")
        args.setdefault("size", 4)
        args.setdefault("min", 0.0)
        args.setdefault("max", 1.0)

    # Search fields stay real StringProperties; the panel draws them with
    # prop_search so they get the data-block widget with its clear button.
    if prop_type == "String" and search_source in SEARCH_SOURCES:
        args.pop("items", None)

    ctor = PROP_TYPE_MAP.get(prop_type)
    args.setdefault("update", _on_property_changed)
    return ctor(**args) if ctor else None


def build_op_storage_class(op_cls):
    annotations = {}
    for prop_name, (prop_type, prop_args) in op_cls.props_definition.items():
        if prop_type in UI_ONLY_TYPES or prop_name in annotations:
            continue
        try:
            prop = _build_prop(prop_type, prop_args)
        except Exception:
            print(f"[{__package__}] Invalid prop '{prop_name}' in {op_cls.id}:")
            traceback.print_exc()
            continue
        if prop is not None:
            annotations[prop_name] = prop
    return type(
        "RefOpsProps_" + storage_attr(op_cls.id),
        (bpy.types.PropertyGroup,),
        {"__annotations__": annotations},
    )


def register_data_storage():
    global _data_storage_cls, _op_storage_classes
    _op_storage_classes = {}
    annotations = {}
    for op_id, op_cls in OPERATIONS.items():
        try:
            sub_cls = build_op_storage_class(op_cls)
            bpy.utils.register_class(sub_cls)
        except Exception:
            print(f"[{__package__}] Failed to register storage for '{op_id}':")
            traceback.print_exc()
            continue
        _op_storage_classes[op_id] = sub_cls
        annotations[storage_attr(op_id)] = bpy.props.PointerProperty(type=sub_cls)

    _data_storage_cls = type(
        "ProceduralDataStorage",
        (bpy.types.PropertyGroup,),
        {"__annotations__": annotations},
    )
    bpy.utils.register_class(_data_storage_cls)
    bpy.types.Scene.ui_factory_props = bpy.props.CollectionProperty(
        type=_data_storage_cls
    )
    bpy.types.Object.procedural_properties = bpy.props.PointerProperty(
        type=_data_storage_cls
    )


def unregister_data_storage():
    global _data_storage_cls, _op_storage_classes
    # Each piece is checked independently — a previous reload that failed partway
    # through (or Blender's own "Reload Scripts") can leave _data_storage_cls set
    # without the Scene/Object properties actually being registered, or vice versa.
    if hasattr(bpy.types.Scene, "ui_factory_props"):
        del bpy.types.Scene.ui_factory_props
    if hasattr(bpy.types.Object, "procedural_properties"):
        del bpy.types.Object.procedural_properties
    if _data_storage_cls is not None:
        try:
            bpy.utils.unregister_class(_data_storage_cls)
        except RuntimeError:
            pass
        _data_storage_cls = None
    # Container first, then the per-operation classes it points at.
    for sub_cls in _op_storage_classes.values():
        try:
            bpy.utils.unregister_class(sub_cls)
        except RuntimeError:
            pass
    _op_storage_classes = {}
