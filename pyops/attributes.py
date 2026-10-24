"""The "Selection Attributes" edit-mode panel data model.

Rows are rebuilt from a depsgraph handler rather than inside draw(), so the
panel renders from a stable collection while still tracking the live selection.
Each row's value properties read and write through bmesh on demand — writes go
to every selected element of the domain, not just the displayed one.

Vector-like attributes (2D/3D/4D vectors, quaternions, colors and 4x4 matrices)
are backed by real FloatVectorProperties instead of one float per component, so
a color renders as a swatch and a matrix can be laid out as a grid.
"""

import traceback

import bmesh
import bpy
import mathutils

from .registry import ATTR_SPECS, DOMAIN_ELEMS

SCALAR_FIELD = {
    "FLOAT": "float_value",
    "INT": "int_value",
    "INT8": "int_value",
    "BOOLEAN": "bool_value",
    "STRING": "string_value",
}

VECTOR_FIELD = {2: "vec2_value", 3: "vec3_value", 4: "vec4_value"}


def spec_for(data_type):
    return ATTR_SPECS.get(data_type)


def resolve_layer(elems, data_type, name):
    """The actual bmesh layer for this attribute, or None.

    Two things can fail and both must count as "not editable": the build may not
    expose an accessor for the type at all (FLOAT2, QUATERNION, FLOAT4X4 ... on
    5.2), or the accessor may exist but not contain this attribute — an INT8
    attribute is not in the `int` collection, so checking only that the accessor
    exists would list the row and then silently read zeros forever."""
    spec = spec_for(data_type)
    if not spec:
        return None
    for accessor in spec[0]:
        layers = getattr(elems.layers, accessor, None)
        if layers is None:
            continue
        layer = layers.get(name)
        if layer is not None:
            return layer
    return None


def field_for(row):
    """Name of the PropertyGroup property that edits this row, or None when the
    row needs custom drawing (matrix)."""
    spec = spec_for(row.data_type)
    if not spec:
        return None
    _, kind, components = spec
    if kind == "scalar":
        return SCALAR_FIELD.get(row.data_type)
    if kind == "color":
        return "color_value"
    if kind == "int_vector":
        return "int2_value"
    if kind == "vector":
        return VECTOR_FIELD.get(components)
    return None  # matrix draws its own grid


def _last_selected_elem(bm, domain):
    """Most recently selected element of a domain, via bmesh's selection history —
    falls back to any selected element if history doesn't cover it (e.g. box-select)."""
    type_map = {
        "POINT": bmesh.types.BMVert,
        "EDGE": bmesh.types.BMEdge,
        "FACE": bmesh.types.BMFace,
    }
    cls = type_map[domain]
    for elem in reversed(bm.select_history):
        if isinstance(elem, cls) and elem.select:
            return elem
    elems = getattr(bm, DOMAIN_ELEMS[domain])
    for e in elems:
        if e.select:
            return e
    return None


def refresh_selection_attribute_rows(context):
    scene = getattr(context, "scene", None)
    if scene is None:
        return

    rows = getattr(scene, "ui_attr_rows", None)
    if rows is None:
        return

    if (
        context.mode != "EDIT_MESH"
        or context.edit_object is None
        or context.edit_object.type != "MESH"
    ):
        rows.clear()
        return

    obj = context.edit_object
    try:
        bm = bmesh.from_edit_mesh(obj.data)
    except Exception:
        rows.clear()
        return

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    rows.clear()
    for domain in ("POINT", "EDGE", "FACE"):
        elems = getattr(bm, DOMAIN_ELEMS[domain])
        selected = [e for e in elems if e.select]
        if not selected:
            continue

        show_hidden = False
        try:
            prefs = bpy.context.preferences.addons[__package__].preferences
            show_hidden = prefs.show_hidden_attributes
        except Exception:
            pass

        attrs = [
            a for a in obj.data.attributes
            if a.domain == domain
            and a.data_type in ATTR_SPECS
            # A leading dot marks Blender's own internal attributes (.position
            # and friends): shown only on request, since they crowd out the ones
            # the user actually painted.
            and (show_hidden or not a.name.startswith("."))
        ]
        if not attrs:
            continue

        target = _last_selected_elem(bm, domain) or selected[-1]
        for attr in attrs:
            item = rows.add()
            item.attr_name = attr.name
            item.domain = domain
            item.data_type = attr.data_type
            item.elem_index = target.index
            # Listed either way — a row the user can see and not edit beats an
            # attribute that silently isn't there.
            item.editable = resolve_layer(elems, attr.data_type, attr.name) is not None


@bpy.app.handlers.persistent
def selection_attribute_rows_update_handler(scene, depsgraph):
    try:
        refresh_selection_attribute_rows(bpy.context)
    except Exception:
        print(f"[{__package__}] Failed to refresh selection attribute rows:")
        traceback.print_exc()

    try:
        # The search mirrors are filled here too: a handler may write to the
        # scene, and draw() may not.
        from . import search

        search.refresh_mirrors(bpy.context)
    except Exception:
        print(f"[{__package__}] Failed to refresh search mirrors:")
        traceback.print_exc()


def _resolve_bm_layer(item):
    obj = bpy.context.edit_object
    if not obj or obj.type != "MESH":
        return None, None
    bm = bmesh.from_edit_mesh(obj.data)
    elems = getattr(bm, DOMAIN_ELEMS.get(item.domain, ""), None)
    if elems is None:
        return None, None
    return elems, resolve_layer(elems, item.data_type, item.attr_name)


def _flatten(value, components):
    """bmesh hands back mathutils types — Vector, Color, Quaternion or Matrix —
    none of which are tuples or lists. Matrix iterates as rows, so it needs a
    second level of unpacking before it lines up with a flat vector property."""
    if value is None:
        return [0.0] * components
    try:
        first = value[0]
    except (TypeError, IndexError, KeyError):
        return [value] + [0.0] * (components - 1)
    if hasattr(first, "__len__"):
        flat = [c for row in value for c in row]
    else:
        flat = list(value)
    if len(flat) < components:
        flat += [0.0] * (components - len(flat))
    return flat[:components]


def _get_attr_value(item, default):
    elems, layer = _resolve_bm_layer(item)
    if elems is None or layer is None:
        return default
    elems.ensure_lookup_table()
    if item.elem_index >= len(elems):
        return default
    return elems[item.elem_index][layer]


def _set_attr_value(item, value):
    """Writes to every selected element of the domain, not just the displayed one."""
    elems, layer = _resolve_bm_layer(item)
    if elems is None or layer is None:
        return
    for e in elems:
        if e.select:
            e[layer] = value
    obj = bpy.context.edit_object
    if obj:
        bmesh.update_edit_mesh(obj.data)


def _get_vector(item, components):
    return _flatten(_get_attr_value(item, None), components)


def _set_vector(item, values):
    elems, layer = _resolve_bm_layer(item)
    if elems is None or layer is None:
        return
    spec = spec_for(item.data_type)
    kind = spec[1] if spec else "vector"
    values = list(values)

    for e in elems:
        if not e.select:
            continue
        if kind == "matrix":
            e[layer] = mathutils.Matrix(
                [values[i : i + 4] for i in range(0, 16, 4)]
            )
            continue
        current = e[layer]
        # Quaternion layers reject a plain list, so mirror whatever type is there.
        if isinstance(current, mathutils.Quaternion):
            e[layer] = mathutils.Quaternion(values)
        else:
            e[layer] = values
    obj = bpy.context.edit_object
    if obj:
        bmesh.update_edit_mesh(obj.data)


class PYOPS_PG_attribute_row(bpy.types.PropertyGroup):
    attr_name: bpy.props.StringProperty()
    domain: bpy.props.StringProperty()
    data_type: bpy.props.StringProperty()
    elem_index: bpy.props.IntProperty()
    editable: bpy.props.BoolProperty(default=True)

    def _get_float(self):
        return _get_attr_value(self, 0.0)

    def _set_float(self, value):
        _set_attr_value(self, value)

    def _get_int(self):
        return _get_attr_value(self, 0)

    def _set_int(self, value):
        _set_attr_value(self, value)

    def _get_bool(self):
        return bool(_get_attr_value(self, False))

    def _set_bool(self, value):
        _set_attr_value(self, value)

    def _get_string(self):
        return _get_attr_value(self, "")

    def _set_string(self, value):
        _set_attr_value(self, value)

    def _get_vec2(self):
        return _get_vector(self, 2)

    def _set_vec2(self, value):
        _set_vector(self, value)

    def _get_vec3(self):
        return _get_vector(self, 3)

    def _set_vec3(self, value):
        _set_vector(self, value)

    def _get_vec4(self):
        return _get_vector(self, 4)

    def _set_vec4(self, value):
        _set_vector(self, value)

    def _get_color(self):
        return _get_vector(self, 4)

    def _set_color(self, value):
        _set_vector(self, value)

    def _get_int2(self):
        return [int(v) for v in _get_vector(self, 2)]

    def _set_int2(self, value):
        _set_vector(self, [int(v) for v in value])

    def _get_matrix(self):
        return _get_vector(self, 16)

    def _set_matrix(self, value):
        _set_vector(self, value)

    float_value: bpy.props.FloatProperty(get=_get_float, set=_set_float)
    int_value: bpy.props.IntProperty(get=_get_int, set=_set_int)
    bool_value: bpy.props.BoolProperty(get=_get_bool, set=_set_bool)
    string_value: bpy.props.StringProperty(get=_get_string, set=_set_string)

    vec2_value: bpy.props.FloatVectorProperty(size=2, get=_get_vec2, set=_set_vec2)
    vec3_value: bpy.props.FloatVectorProperty(size=3, get=_get_vec3, set=_set_vec3)
    vec4_value: bpy.props.FloatVectorProperty(size=4, get=_get_vec4, set=_set_vec4)
    int2_value: bpy.props.IntVectorProperty(size=2, get=_get_int2, set=_set_int2)
    color_value: bpy.props.FloatVectorProperty(
        size=4,
        subtype="COLOR",
        min=0.0,
        max=1.0,
        get=_get_color,
        set=_set_color,
    )
    matrix_value: bpy.props.FloatVectorProperty(
        size=16, get=_get_matrix, set=_set_matrix
    )


classes = (PYOPS_PG_attribute_row,)
