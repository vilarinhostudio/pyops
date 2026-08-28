import bpy
import bmesh

import refops_api

bim = refops_api.bim_helpers


class BonsaiTypeGeometry:
    """Builds IfcTypeProduct entities out of Blender geometry. There is no
    occurrence anywhere in here — the type IS the product this operation makes.

    The update path is the whole point. An element references its type through
    an IfcMappedItem pointing at the type's IfcRepresentationMap, so a type
    deleted and rebuilt takes every one of those references with it. A type
    found by name is therefore kept and only the items of its existing
    representation are swapped — see bim.set_mesh_representation. Nothing is
    ever removed here, not even a type this operation stopped producing: an
    orphan type in the file costs nothing, a broken reference costs a remodel.

    Multiple mode mints one type per value of a painted integer: every face
    carrying the same value becomes one type, however many disconnected islands
    that is. Reading Geometry Nodes instances off the depsgraph was tried first
    and abandoned — a tree whose output is only instances cannot be applied at
    all ("Evaluated geometry from modifier does not contain a mesh"), so that
    geometry never reaches this operation. An attribute survives whatever the
    engine bakes, and it says which parts belong together, which instancing
    never did. Realise the instances in the tree and paint the value.

    Every type keeps the reference object's own local space — its origin is the
    reference object's origin, in both modes. Nothing here recentres anything;
    see _mesh_from_elements for why.

    The 2D representations come from separate objects — the plan symbol of a
    piece of furniture is a drawing, not a section through its body. The same
    painted integer groups them: one drawing can hold every variant's symbol,
    each going to the type carrying its value. This is why the attribute lives
    on POINTS and not on faces — a symbol is a wireframe, and the point domain
    is the only one paintable on geometry with no faces at all. A symbol object
    without the attribute produces one mesh, which then serves every type."""

    id = "BIM_TYPE_GEOMETRY"
    label = "Type Geometry"
    icon = "OUTLINER_OB_GROUP_INSTANCE"
    data_type = "MESH"
    execution_mode = "OBJECT"

    props_definition = {
        "enum_mode": (
            "Enum",
            {
                "name": "Mode",
                "items": [
                    ("SINGLE", "Single Component", "The reference object becomes one type"),
                    ("MULTIPLE", "Multiple Components", "One type per Geometry Nodes instance"),
                ],
                "default": "SINGLE",
            },
        ),
        "sep0": ("Separator", {}),
        "lbl_ifc": ("Label", {"text": "IFC", "icon": "FILE_3D"}),
        "ifc_class": (
            "String",
            {
                "name": "IFC Class",
                "default": "IfcFurniture",
                "description": (
                    "Any IFC class; the Type suffix is added when missing, so "
                    "IfcFurniture and IfcFurnitureType mean the same thing here"
                ),
            },
        ),
        "type_name": (
            "String",
            {
                "name": "Type Name",
                "default": "",
                "description": (
                    "Blank uses the reference object's name. This name is the "
                    "type's identity: a type already in the file answering to "
                    "it is updated, not replaced. Multiple mode appends "
                    "_001, _002, ..."
                ),
            },
        ),
        "group_attr": (
            "String",
            {
                "name": "Group Attr (point int)",
                "search": "attribute",
                "description": (
                    "Integer painted on vertices: everything carrying the same "
                    "value becomes one type, islands included, named "
                    "<Type Name>_<value>. It groups the 2D symbol objects too. "
                    "Blank falls back to one type per loose part"
                ),
                "show_if": ("enum_mode", {"MULTIPLE"}),
            },
        ),
        "sep1": ("Separator", {}),
        "lbl_2d": ("Label", {"text": "2D Representations", "icon": "MESH_DATA"}),
        "use_plan": (
            "Bool",
            {
                "name": "Plan (Annotation2D)",
                "default": False,
                "description": "Symbol drawn in plan, from another object's edges",
            },
        ),
        "plan_object": (
            "Object",
            {"name": "Plan Object", "show_if": ("use_plan", {True})},
        ),
        "use_section": (
            "Bool",
            {
                "name": "Section (Curve3D)",
                "default": False,
                "description": "Symbol drawn in section, from another object's edges",
            },
        ),
        "section_object": (
            "Object",
            {"name": "Section Object", "show_if": ("use_section", {True})},
        ),
        "sep2": ("Separator", {}),
        "lbl_material": ("Label", {"text": "Material", "icon": "MATERIAL"}),
        # Two shapes for one idea. With one type there is one name to type, so a
        # text field says it outright; with one type per instance there is no
        # single answer to type, so the name comes off the painted attribute
        # through the vertex group table. Neither ever creates a set: an
        # unmatched name leaves the type without material.
        "material_set": (
            "String",
            {
                "name": "Constituent Set",
                "default": "",
                "description": (
                    "Name of an IfcMaterialConstituentSet already in the file; "
                    "blank or unmatched leaves the type without material"
                ),
                "show_if": ("enum_mode", {"SINGLE"}),
            },
        ),
        "material_attr": (
            "String",
            {
                "name": "Material Attr (point int)",
                "search": "attribute",
                "description": (
                    "Index into the reference object's vertex groups, painted "
                    "on vertices like the grouping one; the group's name is "
                    "looked up as an IfcMaterialConstituentSet"
                ),
                "show_if": ("enum_mode", {"MULTIPLE"}),
            },
        ),
        "sep3": ("Separator", {}),
        "collection_name": (
            "String",
            {
                "name": "Sub-Collection",
                "default": "",
                "description": (
                    "Optional child of Bonsai's types collection to file the "
                    "type objects into. Blender only, no IFC meaning"
                ),
            },
        ),
    }

    @classmethod
    def execute(cls, container_obj, data_holder):
        if not bim.ensure():
            print("[RefOps] Bonsai indisponível:")
            print(bim.IMPORT_ERROR)
            return False

        parent_obj = bpy.data.objects.get(getattr(container_obj, "proc_parent_name", ""))
        if not parent_obj:
            return False

        ifc_file = bim.get_file()
        if not ifc_file:
            print("[RefOps] Nenhum projeto IFC ativo.")
            return False

        ifc_class = (data_holder.ifc_class or "").strip()
        if not ifc_class.startswith("Ifc"):
            print(f"[RefOps] '{ifc_class}' não parece uma classe IFC.")
            return False
        type_class = ifc_class if ifc_class.endswith("Type") else f"{ifc_class}Type"

        container_obj.display_type = "WIRE"
        container_obj.hide_render = True
        container_obj.name = f"TYPE_CONTAINER_{parent_obj.name}"

        contexts = {
            "body": bim.get_context(ifc_file, "Model", "Body", "MODEL_VIEW"),
            "plan": (
                bim.get_context(ifc_file, "Plan", "Body", "PLAN_VIEW")
                if data_holder.use_plan and data_holder.plan_object
                else None
            ),
            "section": (
                bim.get_context(ifc_file, "Model", "Body", "SECTION_VIEW")
                if data_holder.use_section and data_holder.section_object
                else None
            ),
        }
        if contexts["body"] is None:
            print("[RefOps] Sem contexto Model/Body/MODEL_VIEW no projeto.")
            return False

        base_name = (data_holder.type_name or "").strip() or parent_obj.name
        collection_name = (data_holder.collection_name or "").strip()
        depsgraph = bpy.context.evaluated_depsgraph_get()

        if data_holder.enum_mode == "MULTIPLE":
            return cls._run_multiple(
                ifc_file, container_obj, parent_obj, data_holder, type_class,
                base_name, contexts, collection_name, depsgraph,
            )
        return cls._run_single(
            ifc_file, container_obj, data_holder, type_class, base_name,
            contexts, collection_name, depsgraph,
        )

    # --- MODES ---

    @classmethod
    def _run_single(cls, ifc_file, container_obj, data_holder, type_class,
                    base_name, contexts, collection_name, depsgraph):
        # The CONTAINER's mesh, not the parent's: it is the duplicate the engine
        # made with the parent's viewport modifiers baked in, so it carries the
        # Geometry Nodes output for the current frame. Copied because everything
        # handed to the IFC side is released at the end of the run.
        if not container_obj.data or not container_obj.data.polygons:
            print("[RefOps] Objeto sem geometria de malha.")
            return False

        body = container_obj.data.copy()
        # No grouping attribute in this mode, so every symbol object comes back
        # whole, under the None key.
        symbols = cls._symbol_meshes(data_holder, contexts, depsgraph, "")
        meshes = {"body": body}
        meshes.update({key: by_value[None] for key, by_value in symbols.items()})

        built = cls._build_type(
            ifc_file, type_class, base_name, meshes, contexts,
            (data_holder.material_set or "").strip(), collection_name,
        )
        cls._release([body, *cls._symbol_pool(symbols)])
        return built is not None

    @classmethod
    def _run_multiple(cls, ifc_file, container_obj, parent_obj, data_holder,
                      type_class, base_name, contexts, collection_name, depsgraph):
        # The CONTAINER's mesh again — the parent's live Geometry Nodes tree is
        # never read here. See the class docstring: instanced output never gets
        # baked, so the painted attribute is what carries the grouping.
        if not container_obj.data or not container_obj.data.polygons:
            print("[RefOps] Objeto sem geometria de malha.")
            return False

        group_attr = (data_holder.group_attr or "").strip()
        groups = cls._groups(
            container_obj.data, group_attr,
            (data_holder.material_attr or "").strip(),
        )
        if not groups:
            print("[RefOps] Nenhum grupo encontrado.")
            return False

        symbols = cls._symbol_meshes(data_holder, contexts, depsgraph, group_attr)

        built = 0
        for key, (mesh, material_value) in groups.items():
            meshes = {"body": mesh}
            for context_key, by_value in symbols.items():
                # The symbol painted with this type's value, or the whole object
                # when it carries no value at all. Neither: this type simply has
                # no symbol in that context, and _build_type skips it.
                symbol = by_value.get(key, by_value.get(None))
                if symbol is not None:
                    meshes[context_key] = symbol
            if cls._build_type(
                ifc_file, type_class, f"{base_name}_{key:03d}",
                meshes, contexts,
                cls._constituent_name(parent_obj, material_value),
                collection_name,
            ):
                built += 1

        cls._release([mesh for mesh, _ in groups.values()])
        cls._release(cls._symbol_pool(symbols))
        return built > 0

    # --- TYPE ---

    @classmethod
    def _build_type(cls, ifc_file, type_class, name, meshes, contexts,
                    constituent_name, collection_name):
        """The type, assimilated by name or created. Never removed and rebuilt —
        see the class docstring."""
        type_entity = bim.find_type_by_name(ifc_file, type_class, name)
        obj = bim.object_for(type_entity) if type_entity else None
        if type_entity is None:
            obj, type_entity = bim.create_type(ifc_file, type_class, name)
            if not type_entity:
                return None
        if obj is None:
            # Assimilated, not rebuilt: a type read back from the IFC file has
            # no Blender object — Bonsai lists it in the Type Manager straight
            # from the file and only builds one when something asks for it in
            # the scene. Rebuilding the type to get an object would break every
            # element referencing its representation map.
            obj = bim.link_object(type_entity, name)
        if obj is None:
            print(f"[RefOps] Type '{name}' sem objeto Blender — nada atualizado.")
            return None

        body_rep = None
        for key in ("body", "plan", "section"):
            context = contexts.get(key)
            mesh = meshes.get(key)
            if context is None or mesh is None:
                continue
            carrier = cls._carrier(mesh)
            rep = bim.set_mesh_representation(
                ifc_file, type_entity, carrier, context, mesh
            )
            bpy.data.objects.remove(carrier, do_unlink=True)
            if rep is None:
                # Silence here reads as "the operator worked" while Bonsai
                # offers "No Geometry" when placing an occurrence — the two
                # symptoms are far enough apart to be worth naming.
                print(f"[RefOps] '{name}': representação {key} não foi criada.")
            if key == "body":
                body_rep = rep

        # Only the body reaches the viewport: switching the object to the plan
        # representation would leave the type looking like its own 2D symbol.
        bim.show_representation(obj, body_rep)

        if constituent_name:
            material_set = bim.find_constituent_set(ifc_file, constituent_name)
            if material_set is None:
                print(
                    f"[RefOps] Nenhum IfcMaterialConstituentSet '{constituent_name}' "
                    f"no arquivo — '{name}' fica sem material."
                )
            else:
                bim.assign_material_set(ifc_file, type_entity, material_set)

        # Bonsai's naming, "<Class>/<Name>", and not just for the outliner: its
        # rename callback writes the element's name back from the object's. A
        # bare "Cube" collides with the reference object, Blender hands out
        # "Cube.001", and the callback renames the TYPE to Cube.001 — which is
        # how a refresh stopped finding it by name and minted a second type
        # instead of updating this one.
        obj.name = f"{type_entity.is_a()}/{name}"
        bim.file_in_collection(obj, collection_name)
        return type_entity

    @classmethod
    def _constituent_name(cls, parent_obj, index):
        """The vertex group name an index points at — the string table that
        turns a text field into something paintable."""
        if index is None:
            return ""
        try:
            return bim.vgroup_string(parent_obj, index)
        except ValueError as e:
            print(f"[RefOps] {e}")
            return ""

    @classmethod
    def _carrier(cls, mesh):
        """A throwaway object at the origin holding the mesh.

        add_representation takes a Blender object and some of its paths read
        that object's matrix; handing it anything already placed in the scene
        would bake the placement into the type. A type has no placement — its
        geometry sits at the origin of the representation map — so neither does
        the carrier."""
        obj = bpy.data.objects.new("RefOps Type Carrier", mesh)
        bpy.context.scene.collection.objects.link(obj)
        return obj

    # --- GEOMETRY SOURCES ---

    @classmethod
    def _groups(cls, source, group_attr, material_attr="", fallback=True):
        """One entry per type to build: {key: (mesh, material value)}.

        The grouping integer lives on POINTS. Faces would do for a body, but not
        for the 2D symbols: a plan symbol is a wireframe, and the point domain
        is the only one paintable on geometry that has no faces at all. A face
        or a loose edge belongs to the value most of its vertices carry.

        The key is also the name suffix, so a group keeps its name across
        refreshes as long as the paint doesn't change — which is what lets an
        existing type be found and updated instead of duplicated.

        fallback=False returns nothing when the attribute isn't there, for the
        caller that has something better to do with the whole mesh than chop it
        into loose parts."""
        bm = bmesh.new()
        bm.from_mesh(source)
        bm.verts.ensure_lookup_table()
        # .layers.int carries exactly the INT attributes of that domain and
        # nothing else, so asking it is the domain and type check as well as
        # the lookup.
        layer = bm.verts.layers.int.get(group_attr) if group_attr else None
        material_layer = (
            bm.verts.layers.int.get(material_attr) if material_attr else None
        )

        faces_by_key = {}
        edges_by_key = {}
        if layer is not None:
            for face in bm.faces:
                faces_by_key.setdefault(cls._element_value(face, layer), []).append(face)
            for edge in bm.edges:
                # An edge of a face travels with the face; only loose ones —
                # which is the whole of a wireframe symbol — are placed here.
                if edge.link_faces:
                    continue
                edges_by_key.setdefault(cls._element_value(edge, layer), []).append(edge)
        elif fallback:
            if group_attr:
                print(
                    f"[RefOps] '{group_attr}' não é atributo inteiro de ponto — "
                    f"agrupando por partes soltas."
                )
            for index, island in enumerate(bim.split_islands(list(bm.faces))):
                faces_by_key[index + 1] = island

        groups = {}
        for key in sorted(set(faces_by_key) | set(edges_by_key)):
            faces = faces_by_key.get(key, [])
            mesh = cls._mesh_from_elements(faces, edges_by_key.get(key, []), source)
            if mesh is not None:
                groups[key] = (mesh, cls._dominant_value(faces, material_layer))
        bm.free()
        return groups

    @classmethod
    def _element_value(cls, element, layer):
        """The value a face or an edge carries: the one most of its vertices
        hold, ties going to the lowest.

        A rule rather than a coin toss, so a vertex left unpainted on a seam
        doesn't move a face into a group of its own — and so a refresh lands on
        the same grouping every time, which is what keeps the names stable."""
        counts = {}
        for vert in element.verts:
            value = vert[layer]
            counts[value] = counts.get(value, 0) + 1
        return max(sorted(counts), key=lambda value: counts[value])

    @classmethod
    def _mesh_from_elements(cls, faces, edges, source):
        """A standalone mesh out of a face and loose-edge selection, in the
        reference object's own local space.

        Deliberately NOT recentred. Recentring on the group's own centroid was
        tried and reverted: the pivot then moves with every edit to the mesh, so
        a refresh shifts the geometry of every occurrence already placed from
        the type. Keeping the reference object's frame makes the pivot the one
        thing the operation never touches — the same origin Single mode gives,
        and the price is that a group modelled far from that origin is inserted
        offset from it."""
        index_of = {}
        coords = []

        def index(vert):
            if vert not in index_of:
                index_of[vert] = len(coords)
                coords.append(vert.co.copy())
            return index_of[vert]

        polygons = [[index(v) for v in face.verts] for face in faces]
        segments = [[index(v) for v in edge.verts] for edge in edges]
        if not polygons and not segments:
            return None
        mesh = bpy.data.meshes.new(source.name)
        mesh.from_pydata(coords, segments, polygons)
        for material in source.materials:
            mesh.materials.append(material)
        mesh.update()
        return mesh

    @classmethod
    def _dominant_value(cls, faces, layer):
        """Area-weighted winner of an integer point layer over a group's faces,
        or None without the attribute.

        Each face votes with the value most of its vertices carry — the same
        rule as the grouping — weighted by area, so one large face outweighs a
        cluster of small ones instead of losing to them. A group with no faces
        at all (a wireframe symbol) has no vote and no material.

        Deliberately not bim.dominant(): that helper reads zero as unpainted and
        prefers any non-zero value, which is right for a thickness and wrong
        here — vertex group 0 is the first group in the list, a perfectly
        ordinary choice."""
        if layer is None:
            return None
        weights = {}
        for face in faces:
            value = cls._element_value(face, layer)
            weights[value] = weights.get(value, 0.0) + face.calc_area()
        if not weights:
            return None
        return max(sorted(weights), key=lambda value: weights[value])

    @classmethod
    def _symbol_pool(cls, symbols):
        """Every symbol mesh, flat — what _release needs."""
        return [mesh for by_value in symbols.values() for mesh in by_value.values()]

    @classmethod
    def _symbol_meshes(cls, data_holder, contexts, depsgraph, group_attr):
        """The 2D symbols per context: {context key: {group key: mesh}}.

        Grouped by the same painted integer as the bodies, so one drawing can
        hold every variant's symbol. An object without the attribute comes back
        whole under the None key, and that one mesh then serves every type — no
        fallback to loose parts here, since a symbol split by connectivity would
        just be the same drawing torn apart.

        A context whose object produced nothing is switched off here rather than
        checked again downstream."""
        symbols = {}
        for key, pointer in (
            ("plan", data_holder.plan_object),
            ("section", data_holder.section_object),
        ):
            if contexts.get(key) is None:
                continue
            mesh = cls._bake(pointer.evaluated_get(depsgraph), depsgraph)
            if mesh is None:
                print(f"[RefOps] '{pointer.name}' não produziu malha — {key} ignorado.")
                contexts[key] = None
                continue
            grouped = cls._groups(mesh, group_attr, fallback=False)
            if grouped:
                bpy.data.meshes.remove(mesh)
                symbols[key] = {value: piece for value, (piece, _) in grouped.items()}
            else:
                symbols[key] = {None: mesh}
        return symbols

    @classmethod
    def _bake(cls, evaluated_obj, depsgraph):
        """Evaluated mesh, in the object's own local space.

        Deliberately no polygon check: a plan symbol is a wireframe, and
        rejecting meshes without faces would throw away exactly the geometry the
        2D contexts are there to carry."""
        try:
            mesh = bpy.data.meshes.new_from_object(
                evaluated_obj, preserve_all_data_layers=True, depsgraph=depsgraph
            )
        except Exception as e:
            print(f"[RefOps] new_from_object em '{evaluated_obj.name}': {e}")
            return None
        if mesh is None:
            return None
        if not mesh.vertices:
            bpy.data.meshes.remove(mesh)
            return None
        return mesh

    @classmethod
    def _release(cls, meshes):
        """Every mesh handed to the IFC side is a copy made for this run; the
        carriers that held them are gone by now, so anything still with no user
        is ours to drop."""
        for mesh in meshes:
            try:
                if mesh is not None and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            except (ReferenceError, TypeError):
                pass
