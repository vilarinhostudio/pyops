import bpy
import bmesh
import mathutils

import refops_api

bim = refops_api.bim_helpers
prof = refops_api.bim_profiles

TOL = bim.TOL
# 0/1/2 in the align attribute decides where the reference face sits relative to
# the slab body. Slabs have no equivalent of bim.offset_walls, so this is applied
# as a plain Z shift of the object origin — the extrusion always runs +Z from it.
ALIGN_TOP, ALIGN_CENTER, ALIGN_BOTTOM = 0, 1, 2


class BonsaiSlab:
    """Generates IfcSlab occurrences from horizontal faces of a reference mesh,
    through Bonsai's object lifecycle, so the results are real Bonsai slabs with
    a spatial container, a type carrying the material layer set, and a body the
    Bonsai tools can read.

    Same structure as the wall generator minus join/trim: slabs don't meet each
    other end-to-end, so there are no corners to solve. Openings are the boolean
    cutter's job instead."""

    id = "BIM_SLAB_GENERATOR"
    label = "Bonsai IFC Slab Generator"
    icon = "MESH_PLANE"
    data_type = "MESH"
    execution_mode = "OBJECT"

    props_definition = {
        "lbl_filter": (
            "Label",
            {"text": "Principal", "icon": "RESTRICT_SELECT_OFF"},
        ),
        "bool_attr": ("String", {"name": "Slab Select Attr", "search": "attribute"}),
        "align_attr": ("String", {"name": "Align Attr (int)", "search": "attribute"}),
        "split_edge_attr": (
            "String",
            {
                "name": "Split Attr (edge bool) — optional",
                "search": "attribute",
                "description": (
                    "Edges an element may not cross. Marked edges cut a "
                    "coplanar run into separate elements"
                ),
            },
        ),
        "sep1": ("Separator", {}),
        "lbl_type": ("Label", {"text": "Type Definition", "icon": "MOD_BUILD"}),
        "enum_slab_mode": (
            "Enum",
            {
                "name": "Type Mode",
                "items": [
                    ("C", "Object Type (Pointer)", "Use slab type object pointer"),
                    ("W", "Collection (Weighted)", "Pick a type per element by weight"),
                ],
                "default": "C",
            },
        ),
        "slab_type_pointer": (
            "Object",
            {"name": "Slab Type Ref", "show_if": ("enum_slab_mode", {"C"})},
        ),
        "enum_predefined": (
            "Enum",
            {
                "name": "Predefined Type",
                "items": [
                    ("FLOOR", "Floor", "IfcSlab FLOOR"),
                    ("ROOF", "Roof", "IfcSlab ROOF"),
                    ("LANDING", "Landing", "IfcSlab LANDING"),
                    ("BASESLAB", "Base Slab", "IfcSlab BASESLAB"),
                ],
                "default": "FLOOR",
            },
        ),
        "type_collection": (
            "Collection",
            {"name": "Type Collection", "show_if": ("enum_slab_mode", {"W"})},
        ),
        "type_weight_attr": (
            "String",
            {
                "name": "Type Weight Attr",
                "search": "attribute",
                "show_if": ("enum_slab_mode", {"W"}),
            },
        ),
        "sep_void": ("Separator", {}),
        "void_object": (
            "Object",
            {
                "name": "Common Void",
                "description": (
                    "One mesh cut into every element of this run as a real IFC "
                    "opening — edit it once and all of them follow"
                ),
            },
        ),
        "sep2": ("Separator", {}),
        "lbl_bim_props": (
            "Label",
            {"text": "BIM Properties", "icon": "OUTLINER_OB_GROUP_INSTANCE"},
        ),
        "enum_storey": (
            "Enum",
            {
                "name": "Storey From",
                "items": [
                    ("POINTER", "Object", "One storey for the whole run"),
                    ("ATTRIBUTE", "Attribute", "An index per slab, 0 at the ground"),
                ],
                "default": "POINTER",
            },
        ),
        "story_pointer": (
            "Object",
            {"name": "Building Story", "show_if": ("enum_storey", {"POINTER"})},
        ),
        "storey_attr": (
            "String",
            {
                "name": "Storey Attr (face int)",
                "search": "attribute",
                "description": "Index into the storeys ordered by height, 0 at the ground",
                "show_if": ("enum_storey", {"ATTRIBUTE"}),
            },
        ),
        "precision": ("Int", {"name": "Rounding Decimals", "default": 3}),
    }

    @classmethod
    def execute(cls, container_obj, data_holder):
        if not bim.ensure():
            print("[RefOps] Bonsai indisponível:")
            print(bim.IMPORT_ERROR)
            return False

        parent_obj = bpy.data.objects.get(
            getattr(container_obj, "proc_parent_name", "")
        )
        if not parent_obj or parent_obj.type != "MESH":
            return False

        ifc_file = bim.get_file()
        if not ifc_file:
            print("[RefOps] Nenhum projeto IFC ativo.")
            return False

        container_obj.display_type = "WIRE"
        container_obj.name = f"SLAB_CONTAINER_{parent_obj.name}"

        key = bim.family_key(container_obj, data_holder.bool_attr)
        bim.cleanup_family(ifc_file, "IfcSlab", key)
        bim.cleanup_orphan_objects("SLB_")

        storey_pointer = bim.entity(data_holder.story_pointer)

        # Read the CONTAINER's mesh, not the parent's. The container is the
        # duplicate the engine made with the parent's viewport modifiers baked
        # in, so it carries the Geometry Nodes output for the current frame.
        # parent_obj.data is the original datablock and never changes as the
        # frame advances — reading it would make the result frame-independent.
        source = container_obj

        bm = bmesh.new()
        bm.from_mesh(source.data)
        bm.faces.ensure_lookup_table()

        bool_layer = bm.faces.layers.bool.get(data_holder.bool_attr)
        if not bool_layer:
            bm.free()
            print(
                f"[RefOps] Atributo booleano '{data_holder.bool_attr}' não encontrado."
            )
            return False

        align_layer = (
            bm.faces.layers.int.get(data_holder.align_attr)
            if data_holder.align_attr
            else None
        )

        slab_faces = [f for f in bm.faces if f[bool_layer]]
        if not slab_faces:
            bm.free()
            print(f"[RefOps] Nenhuma face com '{data_holder.bool_attr}' verdadeiro.")
            return False

        # Plane only — see the wall operator: attributes painted on
        # part of a subdivided face must not fragment the element.
        type_objects = prof.ordered_objects(data_holder.type_collection)
        type_weight_layer = (
            bm.faces.layers.float.get(data_holder.type_weight_attr)
            if data_holder.type_weight_attr
            else None
        )
        type_range = None
        if type_weight_layer:
            values = [face[type_weight_layer] for face in slab_faces]
            type_range = (min(values), max(values))

        def int_layer(name):
            return bm.faces.layers.int.get(name) if name else None

        storey_layer = int_layer(data_holder.storey_attr)

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

        groups = bim.group_coplanar_faces(
            slab_faces, source.matrix_world, data_holder.precision
        )
        body_context = bim.get_body_context(ifc_file)

        created = []  # (slab object, type index)
        types = bim.TypeIndexer()
        for group in groups:
            align_value, _mixed = bim.dominant(
                group, align_layer, default=ALIGN_TOP, cast=int
            )

            picked = cls._pick_type(group, type_objects, type_weight_layer, type_range)
            slab_type = cls._resolve_slab_type(
                data_holder.enum_slab_mode,
                data_holder.slab_type_pointer,
                picked,
            )
            if not slab_type:
                continue

            material_set = bim.resolve_type_material(
                slab_type, "IfcMaterialLayerSet"
            )
            # A type without its layer set stops the element outright: the
            # thickness is READ from that set, so there is nothing to extrude.
            if material_set is None:
                continue

            thickness = bim.layer_set_thickness(material_set)
            if thickness <= 0:
                print(
                    f"[RefOps] O IfcMaterialLayerSet de "
                    f"'{slab_type.Name}' soma espessura zero — laje ignorada."
                )
                continue

            # Coplanar faces that aren't connected dissolve into separate
            # regions — each one is its own slab.
            regions = []
            for island in cls._split_by_barrier(group, split_layer):
                regions.extend(bim.dissolve_regions(bm, island))
            if not regions:
                print(
                    f"[RefOps] Grupo de {len(group)} face(s) não dissolveu. "
                    "Faces de laje presas na malha das paredes costumam falhar "
                    "aqui — separe-as ou verifique se são realmente coplanares."
                )
            for slab_face in regions:
                obj = cls._build_slab(
                    ifc_file,
                    slab_face=slab_face,
                    matrix_world=source.matrix_world,
                    slab_type=slab_type,
                    thickness=thickness,
                    align_value=align_value,
                    predefined_type=data_holder.enum_predefined,
                    storey=cls._face_storey(
                        ifc_file, slab_face, storey_layer, storey_pointer, data_holder
                    ),
                    body_context=body_context,
                    key=key,
                )
                if obj:
                    created.append((obj, types.index(slab_type)))

        # One void, cut into every element of the run. A Blender boolean would
        # be invisible to IFC; these are real openings, so they export and can
        # be edited from one source mesh.
        if data_holder.void_object:
            openings = bim.void_elements(
                data_holder.void_object,
                [bim.entity(obj) for obj, _idx in created if bim.entity(obj)],
            )
            for opening in openings:
                bim.tag_family(ifc_file, opening, key)
            print(f"[RefOps] Vazio comum aplicado em {len(openings)} elementos.")

        for obj, type_index in created:
            bim.set_type_index(obj, type_index)

        bm.free()
        return bool(created)

    # --- BUILD ---

    @classmethod
    def _build_slab(
        cls,
        ifc_file,
        slab_face,
        matrix_world,
        slab_type,
        thickness,
        align_value,
        predefined_type,
        storey,
        body_context,
        key,
    ):
        measured = cls._measure_slab_face(slab_face, matrix_world)
        if not measured:
            return None

        elevation, origin_xy, points_2d = measured

        # The extrusion always runs +Z from the origin, so alignment is just
        # where the origin sits relative to the reference face.
        z_offset = {
            ALIGN_TOP: -thickness,
            ALIGN_CENTER: -thickness / 2.0,
            ALIGN_BOTTOM: 0.0,
        }.get(align_value)
        if z_offset is None:
            print(f"[RefOps] Align '{align_value}' fora de 0/1/2 — usando TOP.")
            z_offset = -thickness

        transform = mathutils.Matrix.Translation(
            mathutils.Vector((origin_xy.x, origin_xy.y, elevation + z_offset))
        )

        obj, element = bim.create_product(
            "IfcSlab", "Slab", transform, predefined_type=predefined_type
        )
        if not element:
            return None

        bim.assign_type(ifc_file, element, slab_type)
        bim.assign_usage(ifc_file, element, "IfcMaterialLayerSetUsage")
        bim.assign_container(ifc_file, element, storey)

        representation = bim.swept_solid_representation(
            ifc_file, body_context, points_2d, thickness
        )
        bim.set_representation(ifc_file, obj, element, representation)
        bim.tag_family(ifc_file, element, key)
        return obj

    @classmethod
    def _resolve_slab_type(cls, mode, type_pointer_obj, picked=None):
        """The type is always chosen, never invented — same contract as the wall
        generator. The thickness now comes from the type's layer set, so there
        is nothing left to build a type out of."""
        if mode == "W":
            # Picked per element by weight, the same mapping the profile
            # components use. The collection is the catalogue.
            if picked is None:
                print("[RefOps] Nenhum tipo na collection para esta laje.")
                return None
            entity = bim.entity(picked)
            if entity is None or not entity.is_a("IfcSlabType"):
                print(f"[RefOps] '{picked.name}' não é um IfcSlabType.")
                return None
            return entity

        if not type_pointer_obj:
            print("[RefOps] Nenhum Slab Type Ref definido.")
            return None
        slab_type = bim.entity(type_pointer_obj)
        if slab_type is None or not slab_type.is_a("IfcSlabType"):
            print(f"[RefOps] '{type_pointer_obj.name}' não é um IfcSlabType.")
            return None
        return slab_type

    # --- FINISHING ---

    @classmethod
    def _pick_type(cls, faces, objects, layer, weight_range):
        """One type per element, chosen by the group's own weight.

        The weight is averaged over the group rather than read from one face —
        a coplanar run is a single element and one stray value should not decide
        for the whole of it."""
        if not objects:
            return None
        if not layer or not weight_range:
            return objects[0]
        total = sum(face[layer] * face.calc_area() for face in faces)
        area = sum(face.calc_area() for face in faces) or 1.0
        low, high = weight_range
        index = prof.weight_to_index(total / area, low, high, len(objects))
        return objects[index] if index is not None else None

    @classmethod
    def _face_storey(cls, ifc_file, face, layer, pointer, data_holder):
        """The storey of one slab, read off the face it was dissolved into.

        Read AFTER the dissolve rather than voted over the group: the dissolved
        face keeps the attributes of the region it came from, and a slab sits
        at one level by definition — there is nothing to average."""
        if data_holder.enum_storey != "ATTRIBUTE" or layer is None:
            return pointer
        try:
            return bim.storey_by_index(int(face[layer]), ifc_file)
        except Exception:
            return pointer

    @classmethod
    def _split_by_barrier(cls, faces, split_layer):
        """Cut a face group where the marked edges say to.

        A coplanar run comes out as one element, which is not always what it
        is: a floor plate spanning a whole storey, a panel that should read as
        several. Faces connect only through edges that are NOT flagged, so each
        marked line becomes a boundary."""
        if split_layer is None:
            return [faces]
        members = set(faces)
        remaining = set(faces)
        islands = []
        while remaining:
            start = remaining.pop()
            island = [start]
            stack = [start]
            while stack:
                face = stack.pop()
                for edge in face.edges:
                    if edge[split_layer]:
                        continue  # a barrier: the element stops here
                    for linked in edge.link_faces:
                        if linked in remaining and linked in members:
                            remaining.discard(linked)
                            island.append(linked)
                            stack.append(linked)
            islands.append(island)
        return islands

    # --- GEOMETRY ---

    @classmethod
    def _measure_slab_face(cls, face, matrix_world):
        """Returns (elevation, origin_xy, local 2D profile).

        The face has to be horizontal enough that its XY projection is a real
        polygon — a near-vertical face collapses to a sliver and would produce a
        degenerate profile. Elevation comes from the highest vertex, so a face
        that isn't perfectly flat still yields a slab whose top touches it.

        The origin sits at the footprint centroid so the object isn't stranded
        far from its geometry, and the profile is wound counter-clockwise, which
        is what IfcArbitraryClosedProfileDef expects for an outward normal."""
        normal = matrix_world.to_3x3() @ face.normal
        if normal.length < TOL:
            return None
        normal.normalize()
        if abs(normal.z) < 0.5:
            print("[RefOps] Face muito vertical para virar laje — ignorada.")
            return None

        verts_world = [matrix_world @ v.co for v in face.verts]
        elevation = max(v.z for v in verts_world)

        origin_xy = mathutils.Vector((0.0, 0.0))
        for v in verts_world:
            origin_xy += mathutils.Vector((v.x, v.y))
        origin_xy /= len(verts_world)

        points = [(v.x - origin_xy.x, v.y - origin_xy.y) for v in verts_world]
        if len(points) < 3:
            return None

        # Shoelace: negative area means clockwise seen from +Z.
        area2 = sum(
            points[i][0] * points[(i + 1) % len(points)][1]
            - points[(i + 1) % len(points)][0] * points[i][1]
            for i in range(len(points))
        )
        if abs(area2) < TOL:
            return None
        if area2 < 0:
            points.reverse()

        return elevation, origin_xy, points
