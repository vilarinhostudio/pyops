"""Builds one "[PyOps] <label>" Geometry Nodes group per operation and writes
them, marked as assets, to assets/pyops_bundle_groups.blend.

Run INSIDE Blender with PyOps enabled (it reads the live props_definition):
    p = r"<repo>/tools/build_bundle_groups.py"
    exec(open(p, encoding="utf-8").read(), {"__file__": p})

Each group mirrors what Bundle to PyOps reads (see pyops/bundle_reader.py):
    - a Prefix input; every bundle item and stored attribute is <prefix><key>
    - attribute props become field inputs, stored on the reference geometry
      with the domain/type from the prop's "attr" key
    - every other prop becomes a Store Bundle Item; enums are a Menu input
      turned into the identifier string by a Menu Switch
    - the output is the reference geometry with the bundle set on it

Operations in MANUAL are not generated: their hand-tuned group is taken from
the open file (found by the pyops_id its bundle declares) and copied as is.
The groups are removed from the open file after writing, so appending them
from the asset library later doesn't produce ".001" duplicates.
"""

import os

import bpy

api = bpy.app.driver_namespace["pyops_api"]
registry = api.registry

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "assets", "pyops_bundle_groups.blend")

SKIP = {"PYOPS_BUNDLE", "SPACE_SYNTAX"}
MANUAL = {"BIM_WALL_GENERATOR", "BIM_SLAB_GENERATOR"}

FIELD_SOCKET = {
    "BOOLEAN": "NodeSocketBool",
    "INT": "NodeSocketInt",
    "FLOAT": "NodeSocketFloat",
    "FLOAT_VECTOR": "NodeSocketVector",
}
# prop type -> (interface socket, bundle item socket_type)
VALUE_SOCKET = {
    "String": ("NodeSocketString", "STRING"),
    "Path": ("NodeSocketString", "STRING"),
    "Int": ("NodeSocketInt", "INT"),
    "Float": ("NodeSocketFloat", "FLOAT"),
    "Bool": ("NodeSocketBool", "BOOLEAN"),
    "Vector": ("NodeSocketVector", "VECTOR"),
    "Object": ("NodeSocketObject", "OBJECT"),
    "Collection": ("NodeSocketCollection", "COLLECTION"),
    "Material": ("NodeSocketMaterial", "MATERIAL"),
    "Enum": ("NodeSocketMenu", "STRING"),
}

LANE = 20
PITCH = 240


def prefix_for(op_id):
    core = op_id
    for cut in ("BIM_",):
        if core.startswith(cut):
            core = core[len(cut):]
    for cut in ("_GENERATOR",):
        if core.endswith(cut):
            core = core[: -len(cut)]
    return core + "_"


def sections(op_cls):
    """[(title, [(key, ptype, args)])] split at each Label, aliases resolved."""
    out = [("Principal", [])]
    for key, (ptype, args) in op_cls.props_definition.items():
        if ptype == "Label":
            out.append((args.get("text", key).strip() or key, []))
            continue
        if ptype in registry.UI_ONLY_TYPES:
            continue
        base, merged = api.properties.resolve_alias(ptype, args)
        out[-1][1].append((key, base, merged))
    return [s for s in out if s[1]]


def is_field(args):
    return args.get("search") == "attribute" and "attr" in args


def new_socket(g, name, stype, parent=None, desc=""):
    s = g.interface.new_socket(name, description=desc, in_out="INPUT", socket_type=stype, parent=parent)
    return s


def set_default(sock, ptype, args):
    if ptype == "Float" and "subtype" in args:
        try:
            sock.subtype = args["subtype"]
        except (AttributeError, TypeError):
            pass
    for attr, key in (("min_value", "min"), ("max_value", "max")):
        if key in args and hasattr(sock, attr):
            sock_val = args[key]
            setattr(sock, attr, sock_val)
    if "default" in args and ptype not in ("Enum",) and hasattr(sock, "default_value"):
        try:
            sock.default_value = args["default"]
        except (TypeError, ValueError):
            pass


def build(op_cls):
    name = f"[PyOps] {op_cls.label}"
    old = bpy.data.node_groups.get(name)
    if old:
        bpy.data.node_groups.remove(old)
    g = bpy.data.node_groups.new(name, "GeometryNodeTree")
    g.description = f"PyOps bundle for '{op_cls.label}' ({op_cls.id}); run by Bundle to PyOps"
    N, L = g.nodes, g.links

    # --- interface ---
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    sp = new_socket(g, "Prefix", "NodeSocketString",
                    desc="Prepended to every bundle item and stored attribute; unique per generator instance")
    sp.default_value = prefix_for(op_cls.id)
    s_ref = new_socket(g, "Reference", "NodeSocketGeometry")

    secs = sections(op_cls)
    socks = {}  # key -> interface socket
    for title, props in secs:
        panel = g.interface.new_panel(title)
        for key, ptype, args in props:
            if is_field(args):
                stype = FIELD_SOCKET[args["attr"][1]]
            else:
                stype = VALUE_SOCKET.get(ptype, ("NodeSocketString", "STRING"))[0]
                if ptype == "Enum" and not isinstance(args.get("items"), (list, tuple)):
                    stype = "NodeSocketString"  # dynamic enum: nothing to build a menu from
            s = new_socket(g, args.get("name", key), stype, panel, args.get("description", ""))
            set_default(s, ptype, args)
            socks[key] = s
    so = new_socket(g, "Refresh Order", "NodeSocketInt",
                    desc="Order among the bundles of the same object")

    def gi_out(gi, sock):
        return next(o for o in gi.outputs if o.identifier == sock.identifier)

    def hide_unused(gi):
        for o in gi.outputs:
            if not o.is_linked:
                o.hide = True

    # --- section frames, lanes ---
    lanes = []  # (key, output socket, y, kind)  kind: field | item | ref
    x = 0
    lane_y = 80
    first = True
    for title, props in secs:
        frame = N.new("NodeFrame")
        frame.label = title.upper()
        top = lane_y + 56 if first else lane_y - 44
        gi = N.new("NodeGroupInput")
        gi.parent = frame
        gi.location_absolute = (x + 30, top - 40)
        if first:
            lanes.append(("pyops_reference", gi_out(gi, s_ref), None, "ref"))
        menus = 0
        outs = []
        for key, ptype, args in props:
            src = gi_out(gi, socks[key])
            if ptype == "Enum" and socks[key].socket_type == "NodeSocketMenu":
                ms = N.new("GeometryNodeMenuSwitch")
                ms.data_type = "STRING"
                ms.parent = frame
                ms.location_absolute = (x + 230, top - 40 - menus * 200)
                menus += 1
                ms.enum_items.clear()
                for ident, label, *_ in args["items"]:
                    ms.enum_items.new(label)
                for (ident, label, *_), sock in zip(args["items"], ms.inputs[1:]):
                    sock.default_value = ident
                L.new(src, ms.inputs["Menu"])
                src = ms.outputs["Output"]
            outs.append((key, src, "field" if is_field(args) else "item"))
        right = x + (430 if menus else 200)
        for key, src, kind in outs:
            lane_y -= LANE
            r = N.new("NodeReroute")
            r.location_absolute = (right + 40, lane_y)
            L.new(src, r.inputs[0])
            lanes.append((key, r.outputs[0], lane_y, kind))
        hide_unused(gi)
        x = right + 120
        first = False

    # order lane: a Group Input of its own, like the settings frame
    gi_o = N.new("NodeGroupInput")
    gi_o.location_absolute = (x, lane_y - 60)
    lane_y -= LANE
    r = N.new("NodeReroute")
    r.location_absolute = (x + 200, lane_y)
    L.new(gi_out(gi_o, so), r.inputs[0])
    lanes.append(("pyops_order", r.outputs[0], lane_y, "item"))
    hide_unused(gi_o)

    X_COL = x + 360
    for i, (key, out, y, kind) in enumerate(lanes):
        if y is None:
            y = 80 + LANE
        r = N.new("NodeReroute")
        r.location_absolute = (X_COL, y)
        L.new(out, r.inputs[0])
        lanes[i] = (key, r.outputs[0], y, kind)
    top_lane = max(l[2] for l in lanes)
    bottom_lane = min(l[2] for l in lanes)
    X0 = X_COL + 160

    def fmt(key, gi, frame, at):
        f = N.new("FunctionNodeFormatString")
        f.parent = frame
        f.format_items.new("STRING", "P")
        f.inputs["Format"].default_value = "{P}" + key
        f.location_absolute = at
        L.new(gi_out(gi, sp), f.inputs["P"])
        return f

    def step(out, y, consumer_x, target):
        r = N.new("NodeReroute")
        r.location_absolute = (consumer_x - 40, y)
        L.new(out, r.inputs[0])
        L.new(r.outputs[0], target)

    # --- field attributes (above the lanes) ---
    by_key = {k: (o, y) for k, o, y, _ in lanes}
    args_of = {k: a for _, props in secs for k, _, a in props}
    fa = N.new("NodeFrame"); fa.label = "FIELD ATTRIBUTES"
    yA, yAf = top_lane + 460, top_lane + 180
    gi_a = N.new("NodeGroupInput"); gi_a.parent = fa; gi_a.location_absolute = (X0, yAf)
    geo, geo_y = by_key["pyops_reference"]
    cx = X0 + 200
    fields = [l for l in lanes if l[3] == "field"]
    for key, out, y, _ in fields:
        domain, dtype = args_of[key]["attr"]
        st = N.new("GeometryNodeStoreNamedAttribute")
        st.parent = fa
        st.data_type = dtype
        st.domain = domain
        st.location_absolute = (cx, yA)
        L.new(fmt(key, gi_a, fa, (cx - 100, yAf)).outputs["String"], st.inputs["Name"])
        if geo_y is not None:  # first store: reference arrives by its lane
            step(geo, geo_y, cx, st.inputs["Geometry"])
        else:
            L.new(geo, st.inputs["Geometry"])
        step(out, y, cx, st.inputs["Value"])
        geo, geo_y = st.outputs["Geometry"], None
        cx += PITCH
    hide_unused(gi_a)
    if not fields:
        N.remove(fa)
        N.remove(gi_a)

    # --- bundle items (below the lanes) ---
    fb = N.new("NodeFrame"); fb.label = "BUNDLE ITEMS"
    yB, yBf = bottom_lane - 140, bottom_lane - 420
    gi_b = N.new("NodeGroupInput"); gi_b.parent = fb; gi_b.location_absolute = (X0, yBf)
    items = [("prefix", None, None, "STRING"), ("pyops_id", None, None, "STRING")]
    for key, out, y, kind in lanes:
        if kind == "item":
            if key == "pyops_order":
                stype = "INT"
            else:
                ptype = next(p for _, props in secs for k, p, _ in props if k == key)
                stype = VALUE_SOCKET.get(ptype, (None, "STRING"))[1]
            items.append((key, out, y, stype))
    items.append(("pyops_reference", None, None, "GEOMETRY"))
    bundle = None
    cx = X0 + 200
    for key, out, y, stype in items:
        sb = N.new("NodeStoreBundleItem")
        sb.parent = fb
        sb.socket_type = stype
        sb.location_absolute = (cx, yB)
        L.new(fmt(key, gi_b, fb, (cx - 100, yBf)).outputs["String"], sb.inputs["Path"])
        if bundle is not None:
            L.new(bundle, sb.inputs["Bundle"])
        if key == "prefix":
            L.new(gi_out(gi_b, sp), sb.inputs["Item"])
        elif key == "pyops_id":
            sb.inputs["Item"].default_value = op_cls.id
        elif key == "pyops_reference":
            L.new(geo, sb.inputs["Item"])
        else:
            step(out, y, cx, sb.inputs["Item"])
        bundle = sb.outputs["Bundle"]
        cx += PITCH
    hide_unused(gi_b)

    sgb = N.new("GeometryNodeSetGeometryBundle")
    sgb.location_absolute = (cx + 60, yA)
    gout = N.new("NodeGroupOutput")
    gout.location_absolute = (cx + 280, yA)
    L.new(geo, sgb.inputs["Geometry"])
    L.new(bundle, sgb.inputs["Bundle"])
    L.new(sgb.outputs["Geometry"], gout.inputs["Geometry"])

    # Menu defaults can only be set once the Menu Switch feeds the socket.
    for _, props in secs:
        for key, ptype, args in props:
            s = socks[key]
            if s.socket_type == "NodeSocketMenu" and "default" in args:
                label = next((it[1] for it in args["items"] if it[0] == args["default"]), None)
                try:
                    s.default_value = label
                except (TypeError, ValueError):
                    pass
    return g


def manual_group(op_id):
    """A hand-made group whose Store Bundle Item chain declares this pyops_id."""
    for g in bpy.data.node_groups:
        if g.bl_idname != "GeometryNodeTree" or g.library:
            continue
        for n in g.nodes:
            if n.bl_idname != "NodeStoreBundleItem" or n.socket_type != "STRING":
                continue
            item = n.inputs["Item"]
            if item.is_linked or getattr(item, "default_value", None) != op_id:
                continue
            path = n.inputs["Path"]
            fmt = path.links[0].from_node if path.is_linked else None
            if fmt and fmt.bl_idname == "FunctionNodeFormatString" and \
                    fmt.inputs["Format"].default_value.endswith("pyops_id"):
                return g
    return None


def main():
    built, missing = [], []
    for op_id, op_cls in sorted(registry.OPERATIONS.items()):
        if op_id in SKIP:
            continue
        if op_id in MANUAL:
            src = manual_group(op_id)
            if src is None:
                missing.append(op_id)
                continue
            name = f"[PyOps] {op_cls.label}"
            old = bpy.data.node_groups.get(name)
            if old and old is not src:
                bpy.data.node_groups.remove(old)
            g = src.copy()
            g.name = name
        else:
            g = build(op_cls)
        g.default_group_node_width = 220
        g.color_tag = "NONE"
        g.asset_mark()
        g.asset_data.description = g.description or op_cls.label
        built.append(g)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    bpy.data.libraries.write(OUT, set(built), fake_user=True, compress=True)
    names = [g.name for g in built]
    for g in built:
        bpy.data.node_groups.remove(g)
    print(f"[PyOps.bundle] {len(names)} grupos em {OUT}")
    for n in names:
        print("   ", n)
    if missing:
        print(f"[PyOps.bundle] sem grupo manual no arquivo aberto (pulados): {missing}")
    return names


main()
