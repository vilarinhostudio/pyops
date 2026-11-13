"""Reads PyOps bundles out of an object's Geometry Nodes, without evaluating them.

Python has no access to a bundle's evaluated contents (Blender 5.3:
GeometrySet exposes mesh/curves/pointcloud/volume/instances, nothing for
bundles). So the bundle is read as a *declaration in the node tree*: the chain
of Store Bundle Item / Combine Bundle nodes that reaches the modifier output is
walked upstream, and every item value is resolved statically — reroutes, group
boundaries, constant switches, Format String. Anything that would need real
evaluation is reported as unresolved instead of guessed.

Fields never come through here. A field is only values once evaluated on a
geometry, so the generator groups store them as named attributes
(`<prefix><key>`) and the operator reads those off the baked mesh.

This module is the only thing to replace if Blender ever exposes evaluated
bundles to Python: keep `read_bundles(obj)` and its return shape.
"""

ID_KEY = "pyops_id"
PREFIX_KEY = "prefix"


class Unresolved(Exception):
    """A value that only real evaluation could produce."""


def read_bundles(obj):
    """Every PyOps bundle wired to the output of obj's enabled GN modifiers.

    Returns a list of dicts: {prefix, op_id, order, values, errors, ok}. `values`
    maps the bare key (prefix stripped) to the resolved value; items that could
    not be resolved are left out and explained in `errors`. `ok` is False when
    the bundle must not run at all (its prefix is shared)."""
    items = {}  # path -> (socket, stack, socket_type)
    errors = []
    for mod in obj.modifiers:
        if mod.type != "NODES" or not mod.show_viewport or not mod.node_group:
            continue
        for sgb, stack in _reachable(mod.node_group, "GeometryNodeSetGeometryBundle"):
            _collect(sgb.inputs["Bundle"], stack, mod, items, errors)

    prefixes = sorted(
        (path[: -len(ID_KEY)] for path in items if path.endswith(ID_KEY)),
        key=len,
        reverse=True,
    )
    bundles = {p: {"prefix": p, "values": {}, "errors": list(errors)} for p in prefixes}
    for path, entry in items.items():
        owner = next((p for p in prefixes if path.startswith(p)), None)
        if owner is None or entry is None:
            if entry is None and owner in bundles:
                bundles[owner]["duplicate"] = True
            continue
        sock, stack, mod, stype = entry
        key = path[len(owner):]
        if stype == "GEOMETRY":
            continue  # pyops_reference: reserved until bundles are readable
        try:
            bundles[owner]["values"][key] = _value(sock, stack, mod)
        except Unresolved as e:
            bundles[owner]["errors"].append(f"'{path}': {e}")

    result = []
    for p, b in bundles.items():
        b["ok"] = not b.pop("duplicate", False)
        if not b["ok"]:
            b["errors"].append(f"prefix '{p}' used by more than one generator")
        values = b["values"]
        b["op_id"] = values.pop(ID_KEY, "")
        b["order"] = values.pop("pyops_order", 0) or 0
        declared = values.pop(PREFIX_KEY, p)
        if declared != p:
            b["errors"].append(f"item '{p}{PREFIX_KEY}' = '{declared}' differs from its own path")
        values.pop("pyops_reference", None)
        result.append(b)
    return result


# --- discovery ---


def _active_output(tree):
    outs = [n for n in tree.nodes if n.bl_idname == "NodeGroupOutput"]
    return next((n for n in outs if n.is_active_output), outs[0] if outs else None)


def _reachable(tree, bl_idname):
    """Nodes of one type that feed the tree's active output, nested groups
    included, each with the stack of group nodes it sits under. A generator
    left unconnected in the tree is not reachable, so it does not run."""
    found = []
    seen = set()
    out = _active_output(tree)
    todo = [(s, ()) for s in out.inputs] if out else []
    while todo:
        sock, stack = todo.pop()
        for link in sock.links:
            if link.is_muted:
                continue
            node = link.from_node
            key = (node.as_pointer(), tuple(n.as_pointer() for n in stack))
            if key in seen:
                continue
            seen.add(key)
            if node.bl_idname == bl_idname:
                found.append((node, stack))
            if node.bl_idname == "GeometryNodeGroup" and node.node_tree:
                inner = _active_output(node.node_tree)
                if inner:
                    todo += [(s, stack + (node,)) for s in inner.inputs]
            elif node.bl_idname == "NodeGroupInput" and stack:
                parent = stack[-1]
                todo += [
                    (s, stack[:-1])
                    for s in parent.inputs
                    if s.identifier == link.from_socket.identifier
                ]
            else:
                todo += [(s, stack) for s in node.inputs]
    return found


def _collect(bundle_sock, stack, mod, items, errors):
    """Walk a bundle socket upstream, registering every item it carries."""
    for link in bundle_sock.links:
        if link.is_muted:
            continue
        node, out = link.from_node, link.from_socket
        kind = node.bl_idname
        if kind == "NodeReroute":
            _collect(node.inputs[0], stack, mod, items, errors)
        elif kind == "NodeStoreBundleItem":
            _collect(node.inputs["Bundle"], stack, mod, items, errors)
            try:
                path = _value(node.inputs["Path"], stack, mod)
            except Unresolved as e:
                errors.append(f"path of '{node.name}': {e}")
                continue
            if path in items:
                # Two generators with the same prefix: the later one would
                # silently overwrite the first, so neither runs.
                errors.append(f"duplicate item '{path}' — give each generator its own prefix")
                items[path] = None
                continue
            items[path] = (node.inputs["Item"], stack, mod, node.socket_type)
        elif kind == "NodeCombineBundle":
            for sock, item in zip(node.inputs, node.bundle_items):
                items[item.name] = (sock, stack, mod, item.socket_type)
        elif kind == "NodeJoinBundle":
            _collect(node.inputs[0], stack, mod, items, errors)
        elif kind == "GeometryNodeGroup" and node.node_tree:
            inner = _active_output(node.node_tree)
            sock = inner and next((s for s in inner.inputs if s.identifier == out.identifier), None)
            if sock is not None:
                _collect(sock, stack + (node,), mod, items, errors)
        elif kind == "NodeGroupInput" and stack:
            parent = stack[-1]
            sock = next((s for s in parent.inputs if s.identifier == out.identifier), None)
            if sock is not None:
                _collect(sock, stack[:-1], mod, items, errors)
        else:
            errors.append(f"bundle passes through '{node.name}' ({kind}), not followed")


# --- static value resolution ---


def _value(sock, stack, mod):
    links = [l for l in sock.links if not l.is_muted]
    if links:
        return _output(links[0].from_socket, stack, mod)
    return getattr(sock, "default_value", None)


def _output(out, stack, mod):
    node = out.node
    kind = node.bl_idname
    if node.mute:
        raise Unresolved(f"'{node.name}' is muted")

    if kind == "NodeReroute":
        return _value(node.inputs[0], stack, mod)

    if kind == "NodeGroupInput":
        if stack:
            parent = stack[-1]
            sock = next(s for s in parent.inputs if s.identifier == out.identifier)
            return _value(sock, stack[:-1], mod)
        return _modifier_input(mod, out.identifier)

    if kind == "GeometryNodeGroup":
        inner = _active_output(node.node_tree) if node.node_tree else None
        if inner is None:
            raise Unresolved(f"group '{node.name}' has no output")
        sock = next(s for s in inner.inputs if s.identifier == out.identifier)
        return _value(sock, stack + (node,), mod)

    if kind == "GeometryNodeSwitch":
        on = _value(node.inputs["Switch"], stack, mod)
        return _value(node.inputs["True" if on else "False"], stack, mod)

    if kind == "GeometryNodeMenuSwitch":
        chosen = _value(node.inputs["Menu"], stack, mod)
        if out.name != "Output" and out.bl_idname == "NodeSocketBool":
            return out.name == chosen  # the per-item "is selected" outputs
        sock = next((s for s in node.inputs[1:] if s.name == chosen), None)
        if sock is None:
            raise Unresolved(f"menu value '{chosen}' not found in '{node.name}'")
        return _value(sock, stack, mod)

    if kind == "FunctionNodeFormatString":
        fmt = _value(node.inputs["Format"], stack, mod)
        args = {s.name: _value(s, stack, mod) for s in node.inputs[1:] if s.name}
        try:
            return fmt.format(**args)
        except (KeyError, IndexError, ValueError) as e:
            raise Unresolved(f"format '{fmt}' in '{node.name}': {e}")

    if kind == "GeometryNodeStringJoin":
        delim = _value(node.inputs["Delimiter"], stack, mod)
        strings = node.inputs["Strings"]
        links = sorted(
            (l for l in strings.links if not l.is_muted),
            key=lambda l: getattr(l, "multi_input_sort_id", 0),
            reverse=True,
        )
        return delim.join(str(_output(l.from_socket, stack, mod)) for l in links)

    constants = {
        "FunctionNodeInputString": "string",
        "FunctionNodeInputBool": "boolean",
        "FunctionNodeInputInt": "integer",
        "GeometryNodeInputObject": "object",
        "GeometryNodeInputCollection": "collection",
    }
    if kind in constants:
        return getattr(node, constants[kind])

    raise Unresolved(f"comes from '{node.name}' ({kind}), which needs evaluation")


def _modifier_input(mod, identifier):
    """A top-level group input: its value lives on the modifier. 5.2 moved it
    from mod[identifier] to mod.properties.inputs[identifier]["value"]."""
    try:
        return mod.properties.inputs[identifier].get("value")
    except (AttributeError, KeyError, TypeError):
        pass
    try:
        return mod[identifier]
    except (KeyError, TypeError):
        raise Unresolved(f"modifier input '{identifier}' not readable")
