import bpy
import bmesh
import mathutils

import refops_api

bim = refops_api.bim_helpers

TOL = bim.TOL

# Solidify grows along the face normal from a reference plane set by `offset`,
# which runs -1 (all inside) to +1 (all outside). Position is an input, not an
# attribute, because it describes how the whole component sits against its
# reference surface, not something that varies face by face.
OFFSET_BY_POS = {0: 0.0, 1: -1.0, 2: 1.0}

# Cleanup sweeps IfcElement rather than a fixed list: by_type includes
# subclasses, so a refresh finds the previous run's output whatever class it was
# emitted as. Without this, switching the class field would strand everything
# the operator had already produced.
SWEEP_CLASS = "IfcElement"


class BonsaiFaceComponent:
    """General-purpose extruder: takes the faces flagged by a boolean attribute,
    thickens them with a Solidify modifier, and only then decides what IFC class
    the result is.

    Unlike the wall and slab generators, the body is modelled in Blender and
    handed to IFC, not generated from IFC parameters. That buys arbitrary,
    non-planar shapes (a multi-slope roof, a ramp) at the cost of the parametric
    editing Bonsai gives a wall — these come out as plain swept bodies."""

    id = "BIM_FACE_COMPONENT"
    label = "Bonsai IFC Face Component"
    icon = "MOD_SOLIDIFY"
    data_type = "MESH"
    execution_mode = "OBJECT"

    props_definition = {
        "lbl_filter": ("Label", {"text": "Face Selection", "icon": "RESTRICT_SELECT_OFF"}),
        "bool_attr": ("String", {"name": "Select Attr (face bool)", "search": "attribute"}),
        "split_edge_attr": (
            "String",
            {
                "name": "Split Attr (edge bool) — optional",
                "search": "attribute",
                "description": (
                    "Edges an element may not cross. Marked edges cut a "
                    "connected run into separate elements"
                ),
            },
        ),
        "sep1": ("Separator", {}),
        "lbl_thick": ("Label", {"text": "Thickness Source", "icon": "MOD_SOLIDIFY"}),
        "enum_source": (
            "Enum",
            {
                "name": "Source",
                "items": [
                    ("VERTEX", "Vertex", "Float attribute on vertices, in metres"),
                    ("VGROUP", "Vertex Group", "Existing vertex group weights"),
                    ("FACE", "Face", "Float attribute on faces, in metres"),
                ],
                "default": "FACE",
            },
        ),
        "vertex_attr": (
            "String",
            {
                "name": "Vertex Attr",
                "search": "attribute",
                "show_if": ("enum_source", {"VERTEX"}),
            },
        ),
        "vgroup_name": (
            "String",
            {
                "name": "Vertex Group",
                "search": "vertex_group",
                "show_if": ("enum_source", {"VGROUP"}),
            },
        ),
        "base_thickness": (
            "Distance",
            {
                "name": "Weight = 1.0 Thickness",
                "default": 1.0,
                "min": 0.0,
                "show_if": ("enum_source", {"VGROUP"}),
            },
        ),
        "face_attr": (
            "String",
            {
                "name": "Face Attr",
                "search": "attribute",
                "show_if": ("enum_source", {"FACE"}),
            },
        ),
        "pos": ("Int", {"name": "Position (0/1/2)", "default": 0, "min": 0, "max": 2}),
        "enum_extrude": (
            "Enum",
            {
                "name": "Direction",
                "items": [
                    ("NORMAL", "Face Normal", "Thicken along each face's own normal"),
                    ("VECTOR", "Vector", "Extrude every face along one fixed direction"),
                ],
                "default": "NORMAL",
            },
        ),
        "extrude_vector": (
            # XYZ, not Direction: the DIRECTION subtype renders as a trackball
            # sphere, which is unusable for typing an axis in.
            "XYZ",
            {
                "name": "Vector (world)",
                "default": (0.0, 0.0, 1.0),
                "show_if": ("enum_extrude", {"VECTOR"}),
            },
        ),
        # mints_type=True: this operator BUILDS its own type and its
        # constituent set, so there is no prior name to rename — the two
        # attributes here name the material and the set, not the same thing
        # twice.
        **bim.material_props("face", mints_type=True),
        "sep2": ("Separator", {}),
        "lbl_ifc": ("Label", {"text": "IFC Class", "icon": "FILE_3D"}),
        "ifc_class": (
            "String",
            {
                "name": "IFC Class",
                "default": "IfcSlab",
                "description": "Any IFC element class, e.g. IfcSlab, IfcCovering, IfcRoof",
            },
        ),
        "story_pointer": ("Object", {"name": "Building Story"}),
        "sep3": ("Separator", {}),
        "enum_renov": (
            "Enum",
            {
                "name": "Renovation Status",
                "items": [
                    ("N", "Not Set", "Leave the status out of the file"),
                    ("A", "Existing", "Existing element"),
                    ("B", "Demolish", "Element to be demolished"),
                    ("C", "Construct", "New construction"),
                ],
                "default": "C",
            },
        ),
        "sep4": ("Separator", {}),
        "cut_material": ("String", {"name": "Cut Material", "search": "material"}),
        "cutter_object": ("Object", {"name": "Cutter Object"}),
        "sep5": ("Separator", {}),
        "apply_solidify": ("Bool", {"name": "Bake Solidify into IFC", "default": True}),
        "sync_styles": ("Bool", {"name": "Sync Materials", "default": True}),
        "precision": ("Int", {"name": "Rounding Decimals", "default": 3}),
    }

    @classmethod
    def execute(cls, container_obj, data_holder):
        if not bim.ensure():
            print("[RefOps] Bonsai indisponível:")
            print(bim.IMPORT_ERROR)
            return False

        parent_obj = bpy.data.objects.get(getattr(container_obj, "proc_parent_name", ""))
        if not parent_obj or parent_obj.type != "MESH":
            return False

        ifc_file = bim.get_file()
        if not ifc_file:
            print("[RefOps] Nenhum projeto IFC ativo.")
            return False

        container_obj.display_type = "WIRE"
        container_obj.hide_render = True
        container_obj.name = f"COMPONENT_CONTAINER_{parent_obj.name}"

        ifc_class = (data_holder.ifc_class or "").strip()
        if not ifc_class.startswith("Ifc"):
            print(f"[RefOps] '{ifc_class}' não parece uma classe IFC.")
            return False
        # Most element classes pair with <Class>Type. When they don't, create_type
        # reports it and the element simply comes out untyped.
        type_class = f"{ifc_class}Type"
        prefix = ifc_class[3:]

        storey = bim.entity(data_holder.story_pointer)

        key = bim.family_key(container_obj, data_holder.bool_attr)
        bim.cleanup_family(ifc_file, SWEEP_CLASS, key)
        bim.cleanup_orphan_objects(f"{prefix}_", exclude=(container_obj,))

        # Read the CONTAINER's mesh, not the parent's. The container is the
        # duplicate the engine made with the parent's viewport modifiers baked
        # in, so it carries the Geometry Nodes output for the current frame.
        # parent_obj.data is the original datablock and never changes as the
        # frame advances — reading it would make the result frame-independent.
        source = container_obj

        bm = bmesh.new()
        bm.from_mesh(source.data)
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()

        bool_layer = bm.faces.layers.bool.get(data_holder.bool_attr)
        if not bool_layer:
            bm.free()
            print(f"[RefOps] Atributo booleano '{data_holder.bool_attr}' não encontrado.")
            return False

        faces = [f for f in bm.faces if f[bool_layer]]
        if not faces:
            bm.free()
            return False

        split_layer = (
            bm.edges.layers.bool.get(data_holder.split_edge_attr)
            if data_holder.split_edge_attr
            else None
        )
        if data_holder.split_edge_attr and split_layer is None:
            print(
                f"[RefOps] Atributo de corte '{data_holder.split_edge_attr}' "
                "não encontrado — nada será dividido."
            )

        def int_layer(name):
            return bm.faces.layers.int.get(name) if name else None

        material_layer = int_layer(data_holder.material_attr)
        mat_name_layer = int_layer(data_holder.mat_name_attr)

        def constituent_name(face):
            return cls._constituent_name(face, source, parent_obj, material_layer)

        plan = cls._plan_thickness(bm, faces, data_holder, constituent_name)
        if plan is None:
            bm.free()
            return False

        body_context = bim.get_body_context(ifc_file)
        created = []
        types = bim.TypeIndexer()
        for group in plan:
            mat_name = group["material_name"]
            blender_mat = bim.get_or_create_blender_material(mat_name)

            # The set name: the second attribute in Use or Create, otherwise the
            # type's own name — there is no set to inherit a name from, since
            # this operator creates it.
            set_name = None
            if data_holder.enum_mat_name == "USE_OR_CREATE":
                set_name = bim.vgroup_name_from_faces(
                    parent_obj, group["faces"], mat_name_layer
                )

            comp_type = cls._resolve_type(
                ifc_file,
                type_class,
                prefix,
                group["nominal"],
                mat_name,
                set_name,
                blender_mat if data_holder.sync_styles else None,
                body_context,
            )
            # A missing type is not fatal: an IFC class whose <Class>Type has
            # no counterpart in the schema still produces a valid element, it
            # just comes out untyped.

            # Disconnected islands are separate elements — one IfcRoof per roof,
            # not one IfcRoof holding two unrelated shells.
            for island in cls._split_islands(group["faces"], split_layer):
                obj = cls._build_component(
                    ifc_file,
                    source=source,
                    faces=island,
                    weight_of=group["weight_of"],
                    thickness=group["thickness"],
                    vgroup=group["vgroup"],
                    pos=data_holder.pos,
                    ifc_class=ifc_class,
                    comp_type=comp_type,
                    storey=storey,
                    renov_code=data_holder.enum_renov,
                    apply_solidify=data_holder.apply_solidify,
                    body_context=body_context,
                    extrude_mode=data_holder.enum_extrude,
                    extrude_vector=mathutils.Vector(data_holder.extrude_vector),
                    key=key,
                )
                if obj:
                    created.append((obj, blender_mat, types.index(comp_type)))

        cut_material = (
            bpy.data.materials.get(data_holder.cut_material)
            if data_holder.cut_material
            else None
        )
        for obj, blender_mat, type_index in created:
            cls._apply_material_slots(obj, cut_material, blender_mat)
            bim.set_type_index(obj, type_index)
            cls._add_boolean_cut(obj, data_holder.cutter_object)

        bm.free()
        return bool(created)

    # --- THICKNESS PLANNING ---

    @classmethod
    def _plan_thickness(cls, bm, faces, data_holder, name_of):
        """Resolves the thickness source into groups, each of which becomes one
        Solidify setup. Returns a list of dicts, or None on a bad input.

        Solidify's vertex group is a MULTIPLIER in 0..1 against the modifier's
        own thickness — it is not an absolute distance. So the two per-vertex
        sources both end up as (base thickness, normalised weights), and the
        per-face source can't use a vertex group at all: a weight lives on a
        vertex shared by faces that may want different thicknesses. Faces are
        split by value instead, one uniform Solidify each, which also matches
        what IFC wants — a distinct thickness is a distinct type.

        Grouping is by the CONSTITUENT NAME, not by the Blender material slot:
        the material now comes from the vertex group table, and keying on the
        slot would let two faces wanting different constituents share a type
        and silently take whichever material was resolved first."""
        source = data_holder.enum_source
        precision = data_holder.precision

        if source == "FACE":
            layer = bm.faces.layers.float.get(data_holder.face_attr)
            if not layer:
                print(f"[RefOps] Atributo de face '{data_holder.face_attr}' não encontrado.")
                return None
            buckets = {}
            for f in faces:
                value = round(f[layer], precision)
                if value <= 0:
                    continue
                buckets.setdefault((value, name_of(f)), []).append(f)
            return [
                {
                    "faces": group,
                    "thickness": value,
                    "nominal": value,
                    "material_name": material_name,
                    "vgroup": None,
                    "weight_of": None,
                }
                for (value, material_name), group in sorted(buckets.items())
            ]

        if source == "VERTEX":
            layer = bm.verts.layers.float.get(data_holder.vertex_attr)
            if not layer:
                print(f"[RefOps] Atributo de vértice '{data_holder.vertex_attr}' não encontrado.")
                return None
            values = [v[layer] for f in faces for v in f.verts]
            peak = max(values) if values else 0.0
            if peak <= 0:
                print("[RefOps] Atributo de vértice sem valores positivos.")
                return None
            # Weights are clamped to 0..1, so the attribute's own maximum becomes
            # the modifier thickness and every weight is a fraction of it. That
            # reproduces the attribute's metres exactly.
            return cls._by_material(
                faces,
                thickness=peak,
                nominal=peak,
                vgroup="RefOps Thickness",
                weight_of=lambda v: max(0.0, min(1.0, v[layer] / peak)),
                name_of=name_of,
            )

        if source == "VGROUP":
            name = data_holder.vgroup_name
            deform = bm.verts.layers.deform.active
            if not name or deform is None:
                print("[RefOps] Objeto de referência não tem vertex groups.")
                return None
            group_index = cls._vgroup_index(data_holder, name)
            if group_index is None:
                return None
            base = data_holder.base_thickness
            if base <= 0:
                print("[RefOps] 'Weight = 1.0 Thickness' precisa ser maior que zero.")
                return None
            return cls._by_material(
                faces,
                thickness=base,
                nominal=base,
                vgroup=name,
                weight_of=lambda v: v[deform].get(group_index, 0.0),
                name_of=name_of,
            )

        return None

    @classmethod
    def _vgroup_index(cls, data_holder, name):
        owner = getattr(data_holder, "id_data", None)
        parent = None
        if isinstance(owner, bpy.types.Object):
            parent = bpy.data.objects.get(owner.proc_parent_name)
        elif isinstance(owner, bpy.types.Scene):
            parent = getattr(owner, "ui_parent", None)
        if not parent or name not in parent.vertex_groups:
            print(f"[RefOps] Vertex group '{name}' não existe no objeto de referência.")
            return None
        return parent.vertex_groups[name].index

    @classmethod
    def _by_material(cls, faces, thickness, nominal, vgroup, weight_of, name_of):
        buckets = {}
        for f in faces:
            buckets.setdefault(name_of(f), []).append(f)
        return [
            {
                "faces": group,
                "thickness": thickness,
                "nominal": nominal,
                "material_name": material_name,
                "vgroup": vgroup,
                "weight_of": weight_of,
            }
            for material_name, group in buckets.items()
        ]

    # --- BUILD ---

    @classmethod
    def _split_islands(cls, faces, split_layer=None):
        """Connected face groups, stopping at the marked edges.

        Disconnected shells were already separate elements; a barrier edge adds
        a cut where the geometry itself continues — a storey line across a
        facade, a joint that should read as two panels."""
        members = set(faces)
        remaining = set(faces)
        islands = []
        while remaining:
            start_face = remaining.pop()
            island = [start_face]
            stack = [start_face]
            while stack:
                face = stack.pop()
                for edge in face.edges:
                    if split_layer is not None and edge[split_layer]:
                        continue  # a barrier: the element stops here
                    for linked in edge.link_faces:
                        if linked in remaining and linked in members:
                            remaining.discard(linked)
                            island.append(linked)
                            stack.append(linked)
            islands.append(island)
        return islands

    @classmethod
    def _build_component(cls, ifc_file, source, faces, weight_of, thickness,
                         vgroup, pos, ifc_class, comp_type, storey, renov_code,
                         apply_solidify, body_context, extrude_mode, extrude_vector,
                         key):
        obj = cls._extract_faces(source, faces, weight_of, vgroup)
        if not obj:
            return None

        if extrude_mode == "VECTOR" and extrude_vector.length > 1e-6:
            # Solidify only ever grows along normals, so a fixed direction has
            # to be built by hand. Always baked — there is no modifier to keep.
            cls._extrude_along_vector(obj, extrude_vector, thickness, vgroup, pos)
        else:
            if extrude_mode == "VECTOR":
                print("[RefOps] Vetor nulo — usando a normal das faces.")
            mod = obj.modifiers.new(name="RefOps Solidify", type="SOLIDIFY")
            # EXTRUDE (Simple) is the mode that honours a vertex group;
            # NON_MANIFOLD ignores it, silently flattening every thickness to
            # the base value.
            mod.solidify_mode = "EXTRUDE"
            mod.thickness = thickness
            mod.offset = OFFSET_BY_POS.get(pos, 0.0)
            mod.use_even_offset = True
            mod.use_rim = True
            mod.use_rim_only = False
            if vgroup:
                mod.vertex_group = vgroup
                # Factor at zero influence — 0 so weight 0 means no thickness
                # rather than full thickness.
                mod.thickness_vertex_group = 0.0

            if apply_solidify:
                try:
                    bim.select([obj], obj)
                    bpy.ops.object.modifier_apply(modifier=mod.name)
                except RuntimeError as e:
                    print(f"[RefOps] modifier_apply Solidify: {e}")

        # add_representation reads the mesh datablock, so a Solidify left live
        # would not reach the IFC body — that's the trade-off behind the
        # "Bake Solidify into IFC" toggle.
        element = bim.assign_ifc_class(
            obj, ifc_class, add_representation=True, context=body_context
        )
        if not element:
            bpy.data.objects.remove(obj, do_unlink=True)
            return None

        if comp_type:
            # No assign_usage here: IfcMaterialConstituentSet has no usage
            # entity in the schema, so the occurrence inherits the set through
            # the type association alone. Asking for an IfcMaterialLayerSetUsage
            # would have built a usage pointing at a layer set that no longer
            # exists on this type.
            bim.assign_type(ifc_file, element, comp_type)
        bim.assign_container(ifc_file, element, storey)
        # "Not Set" leaves the status out of the file entirely.
        status = {"A": "EXISTING", "B": "DEMOLISH", "C": "NEW"}.get(renov_code)
        if status:
            bim.set_status(ifc_file, element, status)
        bim.tag_family(ifc_file, element, key)
        bim.file_object(obj)
        return obj

    @classmethod
    def _extrude_along_vector(cls, obj, world_vector, thickness, vgroup, pos):
        """Prism along one fixed direction instead of per-face normals.

        The vector is given in world space and converted into the object's
        frame. Per-vertex weights still apply, so a vertex-driven thickness
        varies along the extrusion exactly as it would with Solidify — the only
        difference is that every vertex travels the same way."""
        local = (obj.matrix_world.to_3x3().inverted() @ world_vector).normalized()

        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        deform = bm.verts.layers.deform.verify()
        group_index = (
            obj.vertex_groups[vgroup].index
            if vgroup and vgroup in obj.vertex_groups
            else None
        )

        def travel(vert):
            if group_index is None:
                return thickness
            return thickness * vert[deform].get(group_index, 0.0)

        result = bmesh.ops.extrude_face_region(bm, geom=list(bm.faces))
        for element in result["geom"]:
            if isinstance(element, bmesh.types.BMVert):
                element.co += local * travel(element)

        # Position, mirroring Solidify's offset: 0 centred, 1 fully behind the
        # reference surface, 2 fully in front. The extruded copies inherit the
        # weights, so shifting by the same per-vertex amount keeps pairs aligned.
        shift = {0: -0.5, 1: -1.0, 2: 0.0}.get(pos, -0.5)
        if shift:
            for vert in bm.verts:
                vert.co += local * travel(vert) * shift

        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()

    @classmethod
    def _extract_faces(cls, source, faces, weight_of, vgroup):
        """Standalone object holding just these faces, origin at their centroid.

        Built vertex by vertex rather than with bpy.ops.mesh.separate — the
        operator version needs Edit Mode and leaves the selection state of the
        reference object rearranged behind it."""
        index_of = {}
        coords = []
        weights = []
        for f in faces:
            for v in f.verts:
                if v in index_of:
                    continue
                index_of[v] = len(coords)
                coords.append(v.co.copy())
                weights.append(weight_of(v) if weight_of else 0.0)

        polys = [[index_of[v] for v in f.verts] for f in faces]
        if not polys:
            return None

        centroid = mathutils.Vector((0.0, 0.0, 0.0))
        for co in coords:
            centroid += co
        centroid /= len(coords)

        mesh = bpy.data.meshes.new("Component")
        mesh.from_pydata([co - centroid for co in coords], [], polys)
        mesh.update()

        obj = bpy.data.objects.new("Component", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.matrix_world = source.matrix_world @ mathutils.Matrix.Translation(
            centroid
        )

        if vgroup:
            vg = obj.vertex_groups.new(name=vgroup)
            for i, weight in enumerate(weights):
                vg.add([i], weight, "REPLACE")

        return obj

    # --- TYPE ---

    @classmethod
    def _constituent_name(cls, face, source, parent_obj, material_layer):
        """The constituent name for one face.

        With the attribute painted, the vertex group table decides. Without it
        the Blender material slot still does — an operation set up before the
        attribute existed keeps producing the same names."""
        if material_layer is not None:
            try:
                return bim.vgroup_string(parent_obj, int(face[material_layer]))
            except ValueError as e:
                print(f"[RefOps] {e}")
                return "Default_Material"
        materials = source.data.materials
        index = face.material_index
        if index < len(materials) and materials[index]:
            return materials[index].name
        return "Default_Material"

    @classmethod
    def _resolve_type(cls, ifc_file, type_class, prefix, thickness, mat_name,
                      set_name, blender_mat, body_context):
        """The type, reused by name or built fresh with its constituent set.

        Matched by NAME rather than by layer signature: the type now carries an
        IfcMaterialConstituentSet, which has no thicknesses to compare, so
        find_type_by_layers had nothing left to match on. The name already
        encodes thickness and constituent, so it separates the same cases."""
        type_name = f"{prefix}_{int(round(thickness * 1000))}mm_{mat_name}"
        try:
            existing = bim.find_type_by_name(ifc_file, type_class, type_name)
        except Exception:
            return None  # no such type class in this schema
        if existing:
            return existing

        type_obj, comp_type = bim.create_type(ifc_file, type_class, type_name)
        if not comp_type:
            return None

        ifc_mat = bim.get_or_create_material(ifc_file, mat_name)
        if blender_mat:
            bim.sync_material_style(ifc_file, ifc_mat, blender_mat, body_context)
        bim.assign_constituent_set(
            ifc_file, comp_type, set_name or type_name, mat_name
        )
        return comp_type

    # --- FINISHING ---

    @classmethod
    def _apply_material_slots(cls, obj, cut_material, inherited_material):
        """Fixed slot contract, same as the wall and slab generators:
            slot 0 -> cut material
            slot 1 -> material inherited from the reference face"""
        mesh = obj.data
        if not mesh:
            return
        mesh.materials.clear()
        mesh.materials.append(cut_material)
        mesh.materials.append(inherited_material)
        # A slot linked to the OBJECT overrides the mesh's material, so writing
        # mesh.materials would change nothing visible. Bonsai leaves slots that
        # way on the elements it manages.
        for slot in obj.material_slots:
            if slot.link != "DATA":
                slot.link = "DATA"
        for poly in mesh.polygons:
            poly.material_index = 1
        mesh.update()

    @classmethod
    def _add_boolean_cut(cls, obj, cutter):
        if not cutter or cutter is obj:
            return
        mod = obj.modifiers.new(name="RefOps Cut", type="BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.object = cutter
        mod.solver = "EXACT"
        if hasattr(mod, "material_mode"):
            mod.material_mode = "INDEX"
