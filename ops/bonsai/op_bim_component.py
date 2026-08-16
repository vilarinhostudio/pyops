import bpy

import refops_api

bim = refops_api.bim_helpers


class BonsaiComponent:
    """Turns the object you point the operation at into an IFC element — the
    equivalent of Bonsai's "Assign IFC Class", wired into the refresh cycle.

    There is no shaping and no face selection: the parent's geometry is the
    element's geometry, and the parent's origin is the element's origin. That
    last part matters more than it looks — it is what lets a render proxy land
    exactly on the element instead of being fitted to some computed anchor.

    The proxy is the reason the Enum exists: geometry that carries good BIM
    semantics is rarely the geometry you want to render. It is placed as a
    linked duplicate — Alt+D, sharing the original's mesh data-block — so a
    hundred placements cost one mesh in the file.

    Multiple mode reads the instances a Geometry Nodes tree produced and makes
    one element out of each. It goes through the depsgraph rather than a
    realised mesh, because realising merges everything into one shell and
    throws away exactly what is worth keeping: each instance's own transform
    and which object it came from. When no instances are found it falls back to
    splitting the realised geometry into loose parts."""

    id = "BIM_COMPONENT"
    label = "Bonsai IFC Component"
    icon = "OUTLINER_OB_GROUP_INSTANCE"
    data_type = "MESH"
    execution_mode = "OBJECT"

    props_definition = {
        "enum_mode": (
            "Enum",
            {
                "name": "Mode",
                "items": [
                    ("SINGLE", "Single Component", "The parent object becomes one element"),
                    ("MULTIPLE", "Multiple Components", "One element per Geometry Nodes instance"),
                ],
                "default": "SINGLE",
            },
        ),
        "source_object": (
            "Object",
            {
                "name": "Instance Source",
                "description": "Object carrying the instancing nodes; blank uses the parent",
                "show_if": ("enum_mode", {"MULTIPLE"}),
            },
        ),
        "sep0": ("Separator", {}),
        "lbl_ifc": ("Label", {"text": "IFC", "icon": "FILE_3D"}),
        "ifc_class": ("String", {"name": "IFC Class", "default": "IfcFurniture"}),
        "type_name": (
            "String",
            {
                "name": "Type Name",
                "default": "",
                "description": "Blank uses the proxy's name, or the source object's. Multiple mode appends _001, _002, ...",
            },
        ),
        "story_pointer": ("Object", {"name": "Building Story"}),
        "enum_renov": (
            "Enum",
            {
                "name": "Renovation Status",
                "items": [
                    ("A", "Existing", "Existing element"),
                    ("B", "Demolish", "Element to be demolished"),
                    ("C", "Construct", "New construction"),
                ],
                "default": "C",
            },
        ),
        "sep1": ("Separator", {}),
        "lbl_proxy": ("Label", {"text": "Render Proxy", "icon": "LINKED"}),
        "enum_proxy": (
            "Enum",
            {
                "name": "Proxy",
                "items": [
                    ("NONE", "None", "BIM geometry only"),
                    ("OBJECT", "Linked Object", "Place a linked copy at the element's transform"),
                ],
                "default": "NONE",
            },
        ),
        "proxy_object": (
            "Object",
            {"name": "Proxy Object", "show_if": ("enum_proxy", {"OBJECT"})},
        ),
        "proxy_collection": (
            "Collection",
            {"name": "Proxy Collection", "show_if": ("enum_proxy", {"OBJECT"})},
        ),
        "sep2": ("Separator", {}),
        "cut_material": ("String", {"name": "Cut Material", "search": "material"}),
        "cutter_object": ("Object", {"name": "Cutter Object"}),
        "sync_styles": ("Bool", {"name": "Sync Materials", "default": True}),
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

        container_obj.display_type = "WIRE"
        container_obj.hide_render = True
        container_obj.name = f"COMP_CONTAINER_{parent_obj.name}"

        proxy_object = (
            data_holder.proxy_object if data_holder.enum_proxy == "OBJECT" else None
        )

        key = bim.family_key(container_obj, data_holder.ifc_class)
        bim.cleanup_family(ifc_file, "IfcElement", key)
        # Proxies carry no IFC entity, so the pset sweep can't see them. They are
        # stamped with the family key as a custom property instead.
        bim.cleanup_tagged_objects(key)

        body_context = bim.get_body_context(ifc_file)

        base_name = (
            data_holder.type_name.strip()
            or (proxy_object.name if proxy_object else parent_obj.name)
        )
        cut_material = (
            bpy.data.materials.get(data_holder.cut_material)
            if data_holder.cut_material
            else None
        )
        types = bim.TypeIndexer()
        common = {
            "types": types,
            "ifc_file": ifc_file,
            "ifc_class": ifc_class,
            "storey": bim.entity(data_holder.story_pointer),
            "renov_code": data_holder.enum_renov,
            "body_context": body_context,
            "sync_styles": data_holder.sync_styles,
            "cut_material": cut_material,
            "cutter_object": data_holder.cutter_object,
            "proxy_object": proxy_object,
            "proxy_collection": data_holder.proxy_collection,
            "key": key,
        }

        if data_holder.enum_mode == "MULTIPLE":
            source_obj = data_holder.source_object or parent_obj
            batch = cls._instance_batch(source_obj, container_obj)
            if not batch:
                print("[RefOps] Nenhuma instância nem parte solta encontrada.")
                return False
            built = 0
            # One type per instance, numbered — nome_001, nome_002, ...
            width = max(3, len(str(len(batch))))
            for index, (mesh, matrix) in enumerate(batch, start=1):
                if cls._build_element(
                    mesh=mesh,
                    matrix=matrix,
                    type_name=f"{base_name}_{index:0{width}d}",
                    **common,
                ):
                    built += 1
                else:
                    bpy.data.meshes.remove(mesh)
            return built > 0

        # The CONTAINER's mesh, not the parent's: it is the duplicate the engine
        # made with the parent's viewport modifiers baked in, so it carries the
        # Geometry Nodes output for the current frame. It also already sits at
        # the parent's transform, which becomes the element's placement.
        source = container_obj
        if not source.data or not getattr(source.data, "polygons", None):
            print("[RefOps] Objeto sem geometria de malha.")
            return False

        mesh = source.data.copy()
        if not cls._build_element(
            mesh=mesh,
            matrix=source.matrix_world.copy(),
            type_name=base_name,
            **common,
        ):
            bpy.data.meshes.remove(mesh)
            return False
        return True

    # --- INSTANCE READING ---

    @classmethod
    def _instance_batch(cls, source_obj, container_obj):
        """(mesh, matrix) per instance, taken from the depsgraph.

        Each instance's mesh is baked immediately: `inst.object` is only valid
        during its own iteration step, and the matrix has to be copied for the
        same reason."""
        batch = []
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for instance in depsgraph.object_instances:
            if not instance.is_instance:
                continue
            parent = instance.parent
            if parent is None or parent.original != source_obj:
                continue
            instanced = instance.object
            if instanced is None or instanced.type != "MESH":
                continue
            try:
                mesh = bpy.data.meshes.new_from_object(
                    instanced, preserve_all_data_layers=True, depsgraph=depsgraph
                )
            except Exception as e:
                print(f"[RefOps] new_from_object falhou numa instância: {e}")
                continue
            if not mesh.polygons:
                bpy.data.meshes.remove(mesh)
                continue
            batch.append((mesh, instance.matrix_world.copy()))

        if batch:
            return batch
        return cls._loose_part_batch(container_obj)

    @classmethod
    def _loose_part_batch(cls, container_obj):
        """Fallback for geometry whose instances were already realised: split the
        baked mesh into connected shells. Each shell keeps the container's
        transform, since a realised mesh no longer records where each piece
        came from."""
        import bmesh

        if not container_obj.data or not getattr(container_obj.data, "polygons", None):
            return []

        bm = bmesh.new()
        bm.from_mesh(container_obj.data)
        bm.faces.ensure_lookup_table()

        batch = []
        matrix = container_obj.matrix_world.copy()
        for island in bim.split_islands(list(bm.faces)):
            index_of = {}
            coords = []
            for face in island:
                for vert in face.verts:
                    if vert in index_of:
                        continue
                    index_of[vert] = len(coords)
                    coords.append(vert.co.copy())
            polygons = [[index_of[v] for v in face.verts] for face in island]
            if not polygons:
                continue
            mesh = bpy.data.meshes.new(container_obj.name)
            mesh.from_pydata(coords, [], polygons)
            for material in container_obj.data.materials:
                mesh.materials.append(material)
            mesh.update()
            batch.append((mesh, matrix.copy()))
        bm.free()
        return batch

    # --- ELEMENT ---

    @classmethod
    def _build_element(cls, ifc_file, mesh, matrix, type_name, ifc_class, storey,
                       renov_code, body_context, sync_styles, cut_material,
                       cutter_object, proxy_object, proxy_collection, types, key):
        obj = bpy.data.objects.new(type_name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.matrix_world = matrix

        element = bim.assign_ifc_class(
            obj, ifc_class, add_representation=True, context=body_context
        )
        if not element:
            bpy.data.objects.remove(obj, do_unlink=True)
            return None

        material = mesh.materials[0] if mesh.materials else None
        comp_type = cls._assign_type(
            ifc_file,
            element,
            ifc_class,
            type_name,
            material if sync_styles else None,
            body_context,
        )
        bim.assign_container(ifc_file, element, storey)
        bim.set_status(
            ifc_file,
            element,
            {"A": "EXISTING", "B": "DEMOLISH", "C": "NEW"}.get(renov_code, "NEW"),
        )
        bim.tag_family(ifc_file, element, key)
        bim.file_object(obj)

        placed = bim.object_for(element) or obj
        cls._apply_material_slots(placed, cut_material, material)
        cls._add_boolean_cut(placed, cutter_object)
        bim.set_type_index(placed, types.index(comp_type))

        if proxy_object:
            cls._place_proxy(proxy_object, proxy_collection, placed.matrix_world, key)
        return element

    # --- IFC ---

    @classmethod
    def _assign_type(cls, ifc_file, element, ifc_class, type_name, blender_mat,
                     body_context):
        type_class = f"{ifc_class}Type"
        comp_type = bim.find_type_by_name(ifc_file, type_class, type_name)
        if comp_type is None:
            _type_obj, comp_type = bim.create_type(ifc_file, type_class, type_name)
            if comp_type and blender_mat:
                ifc_mat = bim.get_or_create_material(ifc_file, blender_mat.name)
                bim.sync_material_style(ifc_file, ifc_mat, blender_mat, body_context)
        if comp_type:
            bim.assign_type(ifc_file, element, comp_type)
        return comp_type

    # --- PROXY ---

    @classmethod
    def _place_proxy(cls, proxy_object, collection, matrix, key):
        """A new object over the SAME data-block — what Alt+D does. Linked to the
        render collection rather than filed by the Collector, because the proxy
        is not an IFC element and has no business in the storey."""
        proxy = bpy.data.objects.new(f"{proxy_object.name}_proxy", proxy_object.data)
        target = collection or bpy.context.scene.collection
        target.objects.link(proxy)
        proxy.matrix_world = matrix.copy()
        bim.tag_object(proxy, key)
        return proxy

    # --- FINISHING ---

    @classmethod
    def _apply_material_slots(cls, obj, cut_material, inherited_material):
        mesh = obj.data
        if not mesh:
            return
        mesh.materials.clear()
        mesh.materials.append(cut_material)
        mesh.materials.append(inherited_material)
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
