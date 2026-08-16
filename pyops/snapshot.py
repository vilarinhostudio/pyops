"""Preservation of data added manually downstream of an operation.

Refresh deletes and recreates the procedural copy, so anything the user added by
hand on that copy (mesh attributes, vertex groups, custom properties) would be
lost. Captured before the delete, replayed after the rebuild — only for things
that don't already exist on the parent.
"""

from .registry import INTERNAL_KEYS


def _value_field(data_type):
    if "COLOR" in data_type:
        return "color"
    if "VECTOR" in data_type or "FLOAT2" in data_type:
        return "vector"
    return "value"


def snapshot_downstream_data(child_obj, parent_obj):
    """Capture attributes, vertex groups and custom properties present on child."""
    snap = {"attributes": {}, "vertex_groups": {}, "custom_props": {}}
    if not parent_obj or not child_obj or not child_obj.data:
        return snap

    # Force edit-mode changes down into the real mesh before reading it.
    if child_obj.data:
        child_obj.data.update()
    if parent_obj.data:
        parent_obj.data.update()

    p_attrs = {a.name for a in parent_obj.data.attributes} if parent_obj.data else set()
    for attr in child_obj.data.attributes:
        if attr.name in p_attrs:
            continue
        f = _value_field(attr.data_type)
        # Domain is stored too, so the attribute can be recreated exactly.
        snap["attributes"][attr.name] = (
            attr.domain,
            attr.data_type,
            [
                tuple(getattr(d, f)) if f != "value" else getattr(d, f)
                for d in attr.data
            ],
        )

    p_vgs = {vg.name for vg in parent_obj.vertex_groups}
    for vg in child_obj.vertex_groups:
        if vg.name in p_vgs:
            continue
        snap["vertex_groups"][vg.name] = [
            (v.index, g.weight)
            for v in child_obj.data.vertices
            for g in v.groups
            if g.group == vg.index
        ]

    p_keys = set(parent_obj.keys())
    for k in child_obj.keys():
        if k not in p_keys and k not in INTERNAL_KEYS and not k.startswith("_"):
            snap["custom_props"][k] = child_obj[k]
    return snap


def restore_downstream_data(new_obj, snap):
    if not new_obj or not new_obj.data:
        return

    # The operation's result has to be committed before attributes are injected.
    new_obj.data.update()

    for name, (domain, dtype, vals) in snap["attributes"].items():
        # Recreate on the correct domain if the rebuild dropped it.
        if name not in new_obj.data.attributes:
            attr = new_obj.data.attributes.new(name=name, type=dtype, domain=domain)
        else:
            attr = new_obj.data.attributes[name]

        f = _value_field(dtype)
        # Element count can shrink, so writes are clamped to the rebuilt mesh.
        for i, val in enumerate(vals):
            if i < len(attr.data):
                setattr(attr.data[i], f, val)

    for name, weights in snap["vertex_groups"].items():
        if name in new_obj.vertex_groups:
            continue
        vg = new_obj.vertex_groups.new(name=name)
        for v_idx, w in weights:
            if v_idx < len(new_obj.data.vertices):
                vg.add([v_idx], w, "REPLACE")

    for k, v in snap["custom_props"].items():
        new_obj[k] = v

    new_obj.data.update()
