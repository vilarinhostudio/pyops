import types

import bpy


bim = pyops_api.bim_helpers
reader = pyops_api.bundle_reader
registry = pyops_api.registry


class BundleToPyOps:
    """Runs every PyOps operation declared as a bundle in the reference's
    Geometry Nodes, one after another, on this single container.

    Each bundle names its operation (`<prefix>pyops_id`) and carries the
    operation's own props under `<prefix><key>`, so this file knows nothing
    about walls or slabs: the target's props_definition says what to fill.
    Attribute props are the exception — their values are fields, which only
    exist once evaluated, so the generator group stores them on the geometry
    as `<prefix><key>` and the prop just receives that name.

    One container for all of them is safe because every family key carries
    the selection attribute, and that now carries the prefix."""

    id = "PYOPS_BUNDLE"
    label = "Bundle to PyOps"
    icon = "NODETREE"
    data_type = "MESH"
    execution_mode = "OBJECT"

    props_definition = {
        "lbl_info": (
            "Label",
            {"text": "Reads the bundles in the Geometry Nodes", "icon": "INFO"},
        ),
    }

    @classmethod
    def execute(cls, container_obj, data_holder):
        parent = bpy.data.objects.get(getattr(container_obj, "proc_parent_name", ""))
        if parent is None:
            return False

        bundles = reader.read_bundles(parent)
        if not bundles:
            print(
                f"[PyOps.bundle] Nenhum bundle com '<prefixo>{reader.ID_KEY}' ligado "
                f"à saída do Geometry Nodes de '{parent.name}'."
            )
            return False

        cls._clear_previous(container_obj)

        attributes = set(container_obj.data.attributes.keys())
        ran = False
        for bundle in sorted(bundles, key=lambda b: (b["order"], b["prefix"])):
            prefix = bundle["prefix"]
            for error in bundle["errors"]:
                print(f"[PyOps.bundle] {prefix}: {error}")
            if not bundle["ok"]:
                continue
            op_cls = registry.OPERATIONS.get(bundle["op_id"])
            if op_cls is None:
                print(f"[PyOps.bundle] {prefix}: operação '{bundle['op_id']}' não existe.")
                continue
            if op_cls is cls:
                print(f"[PyOps.bundle] {prefix}: um bundle não pode rodar o próprio Bundle to PyOps.")
                continue
            if not registry.accepts_object_type(op_cls, container_obj.type):
                print(f"[PyOps.bundle] {prefix}: '{op_cls.label}' não aceita {container_obj.type}.")
                continue

            holder = cls._holder(op_cls, bundle, attributes)
            print(f"[PyOps.bundle] {prefix}: rodando '{op_cls.label}'.")
            try:
                if getattr(op_cls, "execution_mode", "EDIT") == "EDIT":
                    bpy.ops.object.mode_set(mode="EDIT")
                    ok = op_cls.execute(container_obj, holder)
                    bpy.ops.object.mode_set(mode="OBJECT")
                else:
                    ok = op_cls.execute(container_obj, holder)
            except Exception as e:
                # One broken bundle must not take the others down with it.
                print(f"[PyOps.bundle] {prefix}: '{op_cls.label}' falhou: {e!r}")
                if bpy.context.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                continue
            ran = ran or bool(ok)

        # Each operation renamed the container after itself; the last one won.
        container_obj.name = f"BUNDLE_CONTAINER_{parent.name}"
        return ran

    @staticmethod
    def _clear_previous(container_obj):
        """Everything this container made last time goes before anything runs.

        Each operation only cleans its own family, keyed by prefix — a bundle
        deleted from the tree, renamed or now failing would never be asked
        again, and its walls would stay in the IFC for good. The output is
        exactly what the current bundles produce, nothing older."""
        key = bim.family_key(container_obj) + "::"
        if bim.ensure() and bim.get_file():
            removed = bim.cleanup_family(
                bim.get_file(), "IfcProduct", key, match_prefix=True
            )
            if removed:
                print(f"[PyOps.bundle] {removed} elemento(s) da rodada anterior removido(s).")
        bim.cleanup_tagged_objects(key, match_prefix=True)

    @classmethod
    def _holder(cls, op_cls, bundle, attributes):
        """A stand-in for the op's PropertyGroup: defaults first, then the
        bundle. The ops only read attributes off it, so a namespace does."""
        prefix, values = bundle["prefix"], dict(bundle["values"])
        holder = types.SimpleNamespace()
        for key, (ptype, args) in op_cls.props_definition.items():
            if ptype in registry.UI_ONLY_TYPES:
                continue
            if ptype == "String" and args.get("search") == "attribute":
                # Missing means "not painted": same as leaving an optional
                # attribute field empty in the regular panel.
                name = prefix + key
                setattr(holder, key, name if name in attributes else "")
                values.pop(key, None)
                continue
            value = values.pop(key, cls._default(ptype, args))
            if ptype == "Enum":
                value = cls._enum(args, value, prefix, key)
            setattr(holder, key, value)
        for key in values:
            print(f"[PyOps.bundle] {prefix}: '{prefix}{key}' não é campo de '{op_cls.label}', ignorado.")
        return holder

    @staticmethod
    def _default(ptype, args):
        if "default" in args:
            return args["default"]
        if ptype == "Enum":
            items = args.get("items")
            return items[0][0] if isinstance(items, (list, tuple)) and items else ""
        return {"String": "", "Path": "", "Int": 0, "Float": 0.0, "Bool": False}.get(ptype)

    @staticmethod
    def _enum(args, value, prefix, key):
        """Identifier or label, so the GN side can use either."""
        items = args.get("items")
        if not isinstance(items, (list, tuple)):
            return value  # dynamic enum: nothing to check against
        for ident, label, *_ in items:
            if value in (ident, label):
                return ident
        fallback = args.get("default", items[0][0] if items else "")
        accepted = ", ".join(i[0] for i in items)
        print(f"[PyOps.bundle] {prefix}: '{prefix}{key}' = '{value}' inválido (aceita {accepted}); usando '{fallback}'.")
        return fallback
