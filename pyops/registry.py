"""Shared tables, type predicates and cross-module mutable state.

Nothing here imports another RefOps module — this is the bottom of the
dependency graph. The mutable globals (refresh_lock, pending_refresh) are
read/written as `registry.refresh_lock` from other modules rather than
`from .registry import refresh_lock`, because a `from` import binds a copy of
the value and would never see later assignments.
"""

import re

import bpy

OPERATIONS = {}

PROP_TYPE_MAP = {
    "String": bpy.props.StringProperty,
    "Path": bpy.props.StringProperty,  # directory or file picker — see properties._build_prop
    "Float": bpy.props.FloatProperty,
    "Int": bpy.props.IntProperty,
    "Bool": bpy.props.BoolProperty,
    "Vector": bpy.props.FloatVectorProperty,
    "IntVector": bpy.props.IntVectorProperty,
    "Color": bpy.props.FloatVectorProperty,
    "Enum": bpy.props.EnumProperty,
    "Material": bpy.props.PointerProperty,
    "Object": bpy.props.PointerProperty,
    "Collection": bpy.props.PointerProperty,
}

# Named shortcuts for Blender's numeric subtypes/units, so an operator can say
# ("Distance", {...}) instead of memorising ("Float", {"subtype": "DISTANCE",
# "unit": "LENGTH"}). Anything here can still be overridden per-field, and any
# Float/Int/Vector accepts raw "subtype"/"unit" keys directly — this table is a
# convenience, not a gate.
PROP_ALIASES = {
    # scalars
    "Distance": ("Float", {"subtype": "DISTANCE", "unit": "LENGTH"}),
    "DistanceCamera": ("Float", {"subtype": "DISTANCE_CAMERA", "unit": "CAMERA"}),
    "Angle": ("Float", {"subtype": "ANGLE", "unit": "ROTATION"}),
    "Percentage": ("Float", {"subtype": "PERCENTAGE", "min": 0.0, "max": 100.0}),
    "Factor": ("Float", {"subtype": "FACTOR", "min": 0.0, "max": 1.0}),
    "Time": ("Float", {"subtype": "TIME_ABSOLUTE", "unit": "TIME_ABSOLUTE"}),
    "Power": ("Float", {"subtype": "POWER", "unit": "POWER"}),
    "Temperature": ("Float", {"subtype": "TEMPERATURE", "unit": "TEMPERATURE"}),
    "Area": ("Float", {"unit": "AREA"}),
    "Volume": ("Float", {"unit": "VOLUME"}),
    "Mass": ("Float", {"unit": "MASS"}),
    "Pixel": ("Int", {"subtype": "PIXEL"}),
    "Unsigned": ("Int", {"subtype": "UNSIGNED", "min": 0}),
    # vectors
    "Vector2": ("Vector", {"size": 2}),
    "Vector4": ("Vector", {"size": 4}),
    "IntVector2": ("IntVector", {"size": 2}),
    "Translation": ("Vector", {"subtype": "TRANSLATION", "unit": "LENGTH", "size": 3}),
    "Direction": ("Vector", {"subtype": "DIRECTION", "size": 3}),
    "Euler": ("Vector", {"subtype": "EULER", "unit": "ROTATION", "size": 3}),
    "Quaternion": ("Vector", {"subtype": "QUATERNION", "size": 4}),
    "AxisAngle": ("Vector", {"subtype": "AXISANGLE", "size": 4}),
    "XYZ": ("Vector", {"subtype": "XYZ", "size": 3}),
    "Velocity": ("Vector", {"subtype": "VELOCITY", "unit": "VELOCITY", "size": 3}),
    "Acceleration": (
        "Vector",
        {"subtype": "ACCELERATION", "unit": "ACCELERATION", "size": 3},
    ),
    "Matrix": ("Vector", {"subtype": "MATRIX", "size": 16}),
    "ColorGamma": ("Color", {"subtype": "COLOR_GAMMA", "size": 4, "min": 0.0, "max": 1.0}),
}

# Pseudo-types accepted in an operator's props_definition that are UI-only: they
# never become a real bpy property, they just tell the panel what to draw there.
UI_ONLY_TYPES = {"Label", "Separator", "Space"}

# Mesh attribute types the "Selection Attributes" panel knows about, mapped to
# (candidate bmesh layer accessors, widget kind, component count).
#
# Being listed here does NOT mean the type is editable. bmesh only exposes named
# layer collections for some of them — on Blender 5.2 that is bool, float, int,
# string, float_vector, float_color and color, nothing else. FLOAT2, FLOAT4,
# QUATERNION, FLOAT4X4, INT32_2D and INT8 exist on the mesh and survive
# round-tripping, but there is no Python handle to read or write them per
# element while in Edit Mode.
#
# They are kept in the table anyway: attributes.resolve_layer() tries each
# candidate accessor at runtime and the panel marks the row read-only when none
# resolve. A future Blender that adds the accessor makes the row editable with
# no change here.
ATTR_SPECS = {
    "FLOAT": (("float",), "scalar", 1),
    "INT": (("int",), "scalar", 1),
    "INT8": (("int8",), "scalar", 1),
    "BOOLEAN": (("bool",), "scalar", 1),
    "STRING": (("string",), "scalar", 1),
    "FLOAT_VECTOR": (("float_vector",), "vector", 3),
    "FLOAT2": (("float2",), "vector", 2),
    "FLOAT4": (("float4",), "vector", 4),
    "QUATERNION": (("quaternion",), "vector", 4),
    "FLOAT4X4": (("float4x4",), "matrix", 16),
    "INT32_2D": (("int32_2d", "int2"), "int_vector", 2),
    "FLOAT_COLOR": (("float_color", "color"), "color", 4),
    "BYTE_COLOR": (("byte_color", "float_color", "color"), "color", 4),
}

DOMAIN_ELEMS = {"POINT": "verts", "EDGE": "edges", "FACE": "faces"}

# Object custom-property keys the addon owns — excluded from downstream snapshots
# and from the custom-property search dropdown.
INTERNAL_KEYS = {
    "is_procedural_copy",
    "procedural_op_type",
    "proc_parent_name",
    "procedural_properties",
}

# --- CROSS-MODULE MUTABLE STATE ---
# refresh_lock: set by the engine operator while it runs, read by the live-update
# property callback so a writeback of saved_values doesn't re-trigger a refresh.
refresh_lock = False
pending_refresh = set()
enum_items_cache = {}


def make_active(obj):
    """Sets obj as the real, persistent view-layer active object (not a context
    override) — so it survives being deleted and replaced mid-operator, which is
    exactly what refresh does to the object it targets."""
    vl = bpy.context.view_layer
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    vl.objects.active = obj


def remove_object(obj):
    """Remove an object AND its data-block.

    Blender only garbage-collects zero-user data-blocks on file reload or an
    explicit Purge, so `objects.remove()` on its own strands the mesh. Every
    refresh then leaves one behind and the .blend grows for the whole session —
    invisible until you open the orphan-data view."""
    data = getattr(obj, "data", None)
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is None:
        return
    try:
        if data.users or data.use_fake_user:
            return
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)
    except (ReferenceError, TypeError):
        pass


def chain_depth(obj, limit=64):
    """How many copies sit between this object and the original source.

    Walking proc_parent_name is what makes a real chain order itself: an
    extrude whose parent is a bevel is necessarily depth 2 and must run after
    it. The limit is a cycle guard — proc_parent_name is a name, and nothing
    stops a user from pointing two copies at each other."""
    depth = 0
    current = obj
    seen = {obj.name}
    while depth < limit:
        parent = bpy.data.objects.get(getattr(current, "proc_parent_name", ""))
        if parent is None or not getattr(parent, "is_procedural_copy", False):
            return depth
        if parent.name in seen:
            print(f"[{__package__}] Ciclo de dependência em '{obj.name}'.")
            return depth
        seen.add(parent.name)
        current = parent
        depth += 1
    return depth


def refresh_key(obj):
    """Sort key for a batch refresh: depth first, then the user's own order.

    Depth handles chains, which are unambiguous. It does NOT handle siblings —
    a wall operation and an opening operation both hang off the same reference
    mesh at depth 0, yet the openings need the walls to exist first. That is
    what procedural_order is for: the chain cannot express it, so the user
    states it."""
    return (chain_depth(obj), getattr(obj, "procedural_order", 0), obj.name)


def ordered_copies(objects):
    return sorted(
        (obj for obj in objects if getattr(obj, "is_procedural_copy", False)),
        key=refresh_key,
    )


def storage_attr(op_id):
    return "op_" + re.sub(r"\W", "_", str(op_id)).lower()


def op_holder(container, op_id):
    """Each operation owns its own PropertyGroup, so two operations can declare
    props with the same name without colliding."""
    return getattr(container, storage_attr(op_id), None) if container else None


def accepts_object_type(op_cls, obj_type):
    """data_type may be a single string (old behavior), an iterable of strings,
    or 'ANY' to accept every object type — decided per-operation in its own file."""
    dt = getattr(op_cls, "data_type", "MESH")
    if dt == "ANY":
        return True
    if isinstance(dt, str):
        return dt == obj_type
    return obj_type in dt  # set/tuple/list of type strings
