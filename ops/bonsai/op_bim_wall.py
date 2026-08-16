import bpy
import bmesh
import mathutils

import refops_api

bim = refops_api.bim_helpers
prof = refops_api.bim_profiles

TOL = bim.TOL

# The junction log was what made the corner/T classification diagnosable. It is
# quiet now that the mapping is right, and switching it back on is one line when
# a joint comes out wrong.
DEBUG_JUNCTIONS = False
ALIGN_MAP = {0: "CENTER", 1: "EXTERIOR", 2: "INTERIOR"}


class BonsaiWall:
    """Generates IfcWall occurrences from a reference mesh through Bonsai's own
    object lifecycle, so the results behave as real Bonsai walls: parametric TAB
    editing, spatial container, wall tool. Alignment and corner trimming are
    delegated to Bonsai's operators rather than written into the IFC by hand,
    because those operators also regenerate the geometry."""

    id = "BIM_WALL_GENERATOR"
    label = "Bonsai IFC Wall Generator"
    icon = "MOD_BUILD"
    data_type = "MESH"
    execution_mode = "OBJECT"

    props_definition = {
        "lbl_filter": ("Label", {"text": "Face Selection", "icon": "RESTRICT_SELECT_OFF"}),
        "bool_attr": ("String", {"name": "Wall Select Attr", "search": "attribute"}),
        "sep1": ("Separator", {}),
        "lbl_type": ("Label", {"text": "Type Definition", "icon": "MOD_BUILD"}),
        "enum_wall_mode": (
            "Enum",
            {
                "name": "Type Mode",
                "items": [
                    ("S", "Simple (Attribute)", "Match/create type from thickness attribute"),
                    ("C", "Complex (Pointer)", "Use wall type object pointer"),
                    ("W", "Collection (Weighted)", "Pick a type per element by weight"),
                ],
                "default": "S",
            },
        ),
        "thick_attr": (
            "String",
            {
                "name": "Thickness Attr",
                "search": "attribute",
                "show_if": ("enum_wall_mode", {"S"}),
            },
        ),
        "wall_type_pointer": (
            "Object",
            {"name": "Wall Type Ref", "show_if": ("enum_wall_mode", {"C"})},
        ),
        "type_collection": (
            "Collection",
            {"name": "Type Collection", "show_if": ("enum_wall_mode", {"W"})},
        ),
        "type_weight_attr": (
            "String",
            {
                "name": "Type Weight Attr",
                "search": "attribute",
                "show_if": ("enum_wall_mode", {"W"}),
            },
        ),
        "split_edge_attr": (
            "String",
            {
                "name": "Split Attr (edge bool) — optional",
                "search": "attribute",
                "description": (
                    "Edges a wall may not cross. Mark the slab lines and each "
                    "storey becomes its own wall"
                ),
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
                    ("ATTRIBUTE", "Attribute", "An index per wall, 0 at the ground"),
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
        "enum_renov_source": (
            "Enum",
            {
                "name": "Status From",
                "items": [
                    ("FIXED", "Fixed", "One status for the whole run"),
                    ("ATTRIBUTE", "Attribute", "0 not set, 1 existing, 2 demolish, 3 new"),
                ],
                "default": "FIXED",
            },
        ),
        "renov_attr": (
            "String",
            {
                "name": "Status Attr (face int)",
                "search": "attribute",
                "show_if": ("enum_renov_source", {"ATTRIBUTE"}),
            },
        ),
        "cut_material": ("String", {"name": "Cut Material", "search": "material"}),
        "cutter_object": ("Object", {"name": "Cutter Object"}),
        "align_attr": ("String", {"name": "Align Attr (int)", "search": "attribute"}),
        "do_trim": ("Bool", {"name": "Trim Corners", "default": True}),
        "sync_styles": ("Bool", {"name": "Sync Materials", "default": True}),
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
                "show_if": ("enum_renov_source", {"FIXED"}),
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

        parent_obj = bpy.data.objects.get(getattr(container_obj, "proc_parent_name", ""))
        if not parent_obj or parent_obj.type != "MESH":
            return False

        ifc_file = bim.get_file()
        if not ifc_file:
            print("[RefOps] Nenhum projeto IFC ativo.")
            return False

        container_obj.display_type = "WIRE"
        container_obj.name = f"WALL_CONTAINER_{parent_obj.name}"

        key = bim.family_key(container_obj, data_holder.bool_attr)
        bim.cleanup_family(ifc_file, "IfcWall", key)
        bim.cleanup_orphan_objects("WAL_")

        storey = None
        if data_holder.story_pointer:
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
            print(f"[RefOps] Atributo booleano '{data_holder.bool_attr}' não encontrado.")
            return False

        thick_layer = None
        if data_holder.enum_wall_mode == "S":
            thick_layer = bm.faces.layers.float.get(data_holder.thick_attr)
        align_layer = (
            bm.faces.layers.int.get(data_holder.align_attr)
            if data_holder.align_attr
            else None
        )

        wall_faces = [f for f in bm.faces if f[bool_layer]]
        if not wall_faces:
            bm.free()
            return False

        # Grouped by plane and material ONLY. Making thickness or alignment part
        # of the key split a subdivided wall — carving a door loop leaves several
        # coplanar faces and the attributes are rarely painted on every one of
        # them, so each fragment became its own wall. The values are voted on
        # below instead, weighted by area.
        def int_layer(name):
            return bm.faces.layers.int.get(name) if name else None

        storey_layer = int_layer(data_holder.storey_attr)
        renov_layer = int_layer(data_holder.renov_attr)

        split_layer = (
            bm.edges.layers.bool.get(data_holder.split_edge_attr)
            if data_holder.split_edge_attr
            else None
        )
        if data_holder.split_edge_attr and split_layer is None:
            print(
                f"[RefOps] Atributo de corte '{data_holder.split_edge_attr}' "
                "não encontrado — as paredes não serão divididas."
            )

        type_objects = prof.ordered_objects(data_holder.type_collection)
        type_weight_layer = (
            bm.faces.layers.float.get(data_holder.type_weight_attr)
            if data_holder.type_weight_attr
            else None
        )
        type_range = None
        if type_weight_layer:
            values = [face[type_weight_layer] for face in wall_faces]
            type_range = (min(values), max(values))

        groups = bim.group_coplanar_faces(
            wall_faces, source.matrix_world, data_holder.precision
        )
        body_context = bim.get_body_context(ifc_file)

        island_of_obj = {}   # object name -> the faces it was built from
        align_groups = {}
        # Keyed by IFC entity, not by object: offset_walls and
        # join_walls_intersection can replace the Blender object, leaving any
        # reference taken before them pointing at something that is gone.
        created = []  # (IFC element, inherited Blender material, type index)
        types = bim.TypeIndexer()
        # A coplanar group can hold several disconnected runs of wall, and each
        # run is its own element.
        islands = [island for group in groups for island in bim.split_islands(group)]

        for group in islands:
            thickness, mixed_thickness = bim.dominant(
                group, thick_layer, data_holder.precision, default=0.15
            )
            if thickness <= 0:
                thickness = 0.15
            if mixed_thickness:
                print(
                    f"[RefOps] Espessuras diferentes na mesma parede; "
                    f"usando {thickness}."
                )

            blender_mat = None
            mat_index = group[0].material_index
            if mat_index < len(source.data.materials):
                blender_mat = source.data.materials[mat_index]
            mat_name = blender_mat.name if blender_mat else "Default_Material"

            align_value, mixed_align = bim.dominant(
                group, align_layer, default=0, cast=int
            )
            if mixed_align:
                print(f"[RefOps] Alinhamentos diferentes na mesma parede; usando {align_value}.")

            picked = cls._pick_type(
                group, type_objects, type_weight_layer, type_range
            )
            wall_type = cls._resolve_wall_type(
                ifc_file,
                data_holder.enum_wall_mode,
                data_holder.wall_type_pointer,
                thickness,
                mat_name,
                blender_mat if data_holder.sync_styles else None,
                body_context,
                picked,
            )
            if not wall_type:
                continue

            # One wall per facing. A coplanar group can still hold faces that
            # point different ways, and measuring them together cancels the
            # normal and drops the wall.
            for run in cls._split_runs(group, source.matrix_world, split_layer):
                obj, element = cls._build_wall(
                    ifc_file,
                    faces=run,
                    matrix_world=source.matrix_world,
                    wall_type=wall_type,
                    thickness=thickness,
                    storey=cls._run_storey(
                        ifc_file, run, storey_layer, storey_pointer, data_holder
                    ),
                    body_context=body_context,
                    renov_code=cls._run_status(run, renov_layer, data_holder),
                    key=key,
                )
                if obj:
                    island_of_obj[obj.name] = run
                    align_groups.setdefault(align_value, []).append(obj)
                    created.append((element, blender_mat, types.index(wall_type)))

        # Alignment first: trimming solves the corners against the final body
        # positions, so it has to run after the walls have been offset.
        cls._apply_alignment(align_groups)
        if data_holder.do_trim:
            cls._trim_corners(island_of_obj, source.matrix_world)

        # Slots go LAST. offset_walls and join_walls_intersection both rebuild
        # the mesh from the IFC representation, which wipes material slots —
        # assigning them earlier in the loop silently loses them.
        cut_material = (
            bpy.data.materials.get(data_holder.cut_material)
            if data_holder.cut_material
            else None
        )
        # One void, cut into every element of the run. A Blender boolean would
        # be invisible to IFC; these are real openings, so they export and can
        # be edited from one source mesh.
        if data_holder.void_object:
            openings = bim.void_elements(
                data_holder.void_object, [element for element, _mat, _idx in created]
            )
            for opening in openings:
                bim.tag_family(ifc_file, opening, key)
            print(f"[RefOps] Vazio comum aplicado em {len(openings)} elementos.")

        for element, blender_mat, type_index in created:
            obj = bim.object_for(element)
            if not obj:
                # Bonsai removed it — a merge during the trim, most likely.
                print("[RefOps] Uma parede sumiu durante o trim.")
                continue
            cls._apply_material_slots(obj, cut_material, blender_mat)
            bim.set_type_index(obj, type_index)
            cls._add_boolean_cut(obj, data_holder.cutter_object)

        bm.free()
        return bool(island_of_obj)

    # --- BUILD ---

    @classmethod
    def _build_wall(cls, ifc_file, faces, matrix_world, wall_type, thickness,
                    storey, body_context, renov_code, key):
        measured = cls._measure_wall_group(faces, matrix_world)
        if not measured:
            return None, None
        height, length, tangent, normal, base_origin = measured

        transform = (
            mathutils.Matrix.Translation(base_origin)
            @ mathutils.Matrix((tangent, normal, mathutils.Vector((0.0, 0.0, 1.0))))
            .transposed()
            .to_4x4()
        )

        obj, element = bim.create_product("IfcWall", "Wall", transform)
        if not element:
            return None, None

        bim.assign_type(ifc_file, element, wall_type)
        # The usage on the OCCURRENCE is what makes Bonsai treat this as a
        # parametric wall — without it TAB falls back to plain mesh editing.
        bim.assign_usage(ifc_file, element, "IfcMaterialLayerSetUsage")
        bim.assign_container(ifc_file, element, storey)
        # "Not Set" leaves the status out of the file entirely.
        status = {"A": "EXISTING", "B": "DEMOLISH", "C": "NEW"}.get(renov_code)
        if status:
            bim.set_status(ifc_file, element, status)

        # TODO: clipping planes from non-wall adjacent faces. _find_clip_planes
        # still computes them (world space; convert with transform.inverted()
        # before passing as ifcopenshell.util.data.Clipping in `clippings=`).
        representation = bim.run(
            "geometry.add_wall_representation",
            ifc_file,
            context=body_context,
            length=length,
            height=height,
            thickness=thickness,
        )
        bim.set_representation(ifc_file, obj, element, representation)
        bim.tag_family(ifc_file, element, key)
        return obj, element

    @classmethod
    def _resolve_wall_type(cls, ifc_file, mode, type_pointer_obj, thickness, mat_name,
                           blender_mat, body_context, picked=None):
        if mode == "W":
            # Picked per element by weight, the same mapping the profile
            # components use. The collection is the catalogue.
            if picked is None:
                print("[RefOps] Nenhum tipo na collection para este elemento.")
                return None
            entity = bim.entity(picked)
            if entity is None or not entity.is_a("IfcWallType"):
                print(f"[RefOps] '{picked.name}' não é um IfcWallType.")
                return None
            return entity

        if mode == "C" and type_pointer_obj:
            wt = bim.entity(type_pointer_obj)
            return wt if wt and wt.is_a("IfcWallType") else None

        # Match on characteristics, not name — reuses types made by the Bonsai
        # wall tool or by earlier runs regardless of what they're called.
        existing = bim.find_type_by_layers(
            ifc_file, "IfcWallType", [(thickness, mat_name)]
        )
        if existing:
            return existing

        type_name = f"WAL_{int(round(thickness * 1000))}mm_{mat_name}"
        type_obj, wall_type = bim.create_type(ifc_file, "IfcWallType", type_name)
        if not wall_type:
            return None

        ifc_mat = bim.get_or_create_material(ifc_file, mat_name)
        if blender_mat:
            bim.sync_material_style(ifc_file, ifc_mat, blender_mat, body_context)
        bim.assign_layer_set(ifc_file, wall_type, type_name, [(ifc_mat, thickness)])
        return wall_type

    # --- FINISHING ---

    @classmethod
    def _apply_material_slots(cls, obj, cut_material, inherited_material):
        """Fixed slot contract on every generated wall:
            slot 0 -> cut material
            slot 1 -> material inherited from the reference face

        Both slots are always created, even when a material is missing, because
        the mesh addresses them by index — an empty slot 0 still has to exist or
        every face would shift onto the wrong material. Body faces are pinned to
        slot 1; slot 0 is left for the faces the boolean cutter introduces."""
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
        """Left as a live modifier, not applied — the wall stays parametric and
        the cut follows the cutter when it moves.

        material_mode='INDEX' is what makes the slot contract pay off: faces the
        cutter creates take the material index they carry on the cutter object,
        so a cutter whose faces sit at index 0 paints the cut surfaces with the
        wall's slot 0 (the cut material)."""
        if not cutter or cutter is obj:
            return
        mod = obj.modifiers.new(name="RefOps Cut", type="BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.object = cutter
        mod.solver = "EXACT"
        if hasattr(mod, "material_mode"):
            mod.material_mode = "INDEX"

    # --- BONSAI OPERATORS ---

    @classmethod
    def _apply_alignment(cls, align_groups):
        """Writing OffsetFromReferenceLine by hand moves the reference line but
        not the body — offset_walls does both."""
        props = bpy.context.scene.BIMModelProperties
        previous = props.offset_type_vertical
        for align_value, objs in align_groups.items():
            offset_type = ALIGN_MAP.get(align_value)
            if not offset_type or not objs:
                if objs:
                    print(f"[RefOps] Align '{align_value}' fora de 0/1/2 — ignorado.")
                continue
            try:
                bim.select(objs)
                props.offset_type_vertical = offset_type
                bpy.ops.bim.offset_walls()
            except Exception as e:
                print(f"[RefOps] offset_walls ({offset_type}): {e}")
        try:
            props.offset_type_vertical = previous
        except Exception:
            pass

    @classmethod
    def _trim_corners(cls, island_of_obj, matrix_world):
        """Join walls that meet, choosing the operator the junction calls for.

        The two are not interchangeable:

        * L junction — both walls END at the shared edge. extend_walls_to_wall
          stretches them into each other and the corner closes.
        * T junction — one wall ends against the SIDE of another that carries on
          past it. join_walls_intersection extends and trims, so the through
          wall survives.

        Using the intersection join on an L merged the pair, and using it where
        one wall runs through made the shorter wall cut the longer one in half.
        Which is which is decided by where the shared edge falls along each
        wall's own length, not by guessing."""
        rotation = matrix_world.to_3x3()
        spans = {}
        for name, faces in island_of_obj.items():
            normal = mathutils.Vector((0.0, 0.0, 0.0))
            for face in faces:
                normal += (rotation @ face.normal) * face.calc_area()
            normal.z = 0.0
            if normal.length < TOL:
                continue
            normal.normalize()
            tangent = mathutils.Vector((normal.y, -normal.x, 0.0))
            points = [
                matrix_world @ vert.co for face in faces for vert in face.verts
            ]
            projections = [point.x * tangent.x + point.y * tangent.y for point in points]
            spans[name] = {
                "normal": normal,
                "tangent": tangent,
                "low": min(projections),
                "high": max(projections),
            }

        owners = {}
        edge_point = {}
        for name, faces in island_of_obj.items():
            for face in faces:
                for edge in face.edges:
                    v1, v2 = [vert.co for vert in edge.verts]
                    if abs(v1.x - v2.x) > TOL or abs(v1.y - v2.y) > TOL:
                        continue  # only vertical edges are wall-end junctions
                    owners.setdefault(edge.index, set()).add(name)
                    edge_point[edge.index] = matrix_world @ ((v1 + v2) / 2.0)

        junctions = {}
        for edge_index, names in owners.items():
            ordered = sorted(names)
            point = edge_point[edge_index]
            for first in range(len(ordered)):
                for second in range(first + 1, len(ordered)):
                    name_a, name_b = ordered[first], ordered[second]
                    kind = cls._junction_kind(spans, name_a, name_b, point)
                    if kind:
                        junctions.setdefault((name_a, name_b), kind)

        objs = [
            bpy.data.objects.get(name)
            for name in island_of_obj
            if bpy.data.objects.get(name)
        ]
        if objs:
            try:
                bim.select(objs)
                bpy.ops.bim.unjoin_walls()
            except Exception as e:
                print(f"[RefOps] unjoin_walls: {e}")

        # Corners first. I had this backwards: a corner extension changes where
        # the perimeter walls END, so a T trimmed before that meets a wall whose
        # length then moves out from under it — the divider came out cut to the
        # wrong place and the corners looked mangled.
        ordered_junctions = sorted(
            junctions.items(), key=lambda item: (0 if item[1] == "CORNER" else 1, item[0])
        )

        if DEBUG_JUNCTIONS:
            print(f"[RefOps] {len(ordered_junctions)} junções detectadas:")
        for (name_a, name_b), kind in ordered_junctions:
            a, b = bpy.data.objects.get(name_a), bpy.data.objects.get(name_b)
            if not a or not b:
                continue
            if kind == "CORNER":
                # Two walls that both end here: extended and mitred into a joint.
                selected, active, operator_name = [a, b], b, "join_walls_intersection"
            else:
                # kind names the wall that ENDS; it is extended up to the one
                # running through, which stays active and untouched.
                ending, through = (a, b) if kind == "T_A" else (b, a)
                selected, active = [ending, through], through
                operator_name = "extend_walls_to_wall"

            if DEBUG_JUNCTIONS:
                print(
                    f"[RefOps]   {kind:8} {name_a} + {name_b} -> "
                    f"{operator_name}, ativo={active.name}"
                )
            try:
                bim.select(selected, active)
                getattr(bpy.ops.bim, operator_name)()
            except Exception as e:
                print(f"[RefOps] {operator_name} {name_a}/{name_b}: {e}")

    @classmethod
    def _junction_kind(cls, spans, name_a, name_b, point):
        """CORNER, T_A, T_B — or None when the pair should be left alone.

        A wall "ends" at a point when that point sits at either extreme of its
        own tangent span. Parallel walls are excluded outright: they are two
        runs of one plane, or the two faces of a single wall, and joining them
        merges the pair."""
        span_a, span_b = spans.get(name_a), spans.get(name_b)
        if not span_a or not span_b:
            return None
        if abs(span_a["normal"].dot(span_b["normal"])) > 0.99:
            return None

        ends_a = cls._is_end(span_a, point)
        ends_b = cls._is_end(span_b, point)
        if ends_a and ends_b:
            return "CORNER"
        if ends_a:
            return "T_A"
        if ends_b:
            return "T_B"
        return None  # a crossing, which neither operator handles

    @classmethod
    def _is_end(cls, span, point):
        projection = point.x * span["tangent"].x + point.y * span["tangent"].y
        length = span["high"] - span["low"]
        # Proportional tolerance with a floor, so a long wall isn't judged by
        # millimetres and a short one isn't all "end".
        margin = max(0.05, length * 0.02)
        return (
            abs(projection - span["low"]) <= margin
            or abs(projection - span["high"]) <= margin
        )

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

    # --- GEOMETRY ---

    @classmethod
    def _run_storey(cls, ifc_file, faces, layer, pointer, data_holder):
        """The storey of one wall, from the faces that make it up.

        The LOWEST index wins: a wall crossing a floor line belongs to the
        storey it rises from. Cutting it per storey is what the split attribute
        does, and that is a separate decision."""
        if data_holder.enum_storey != "ATTRIBUTE" or layer is None:
            return pointer
        values = [int(face[layer]) for face in faces]
        if not values:
            return pointer
        return bim.storey_by_index(min(values), ifc_file)

    @classmethod
    def _run_status(cls, faces, layer, data_holder):
        """Renovation status by area-weighted majority over the wall."""
        if data_holder.enum_renov_source != "ATTRIBUTE" or layer is None:
            return data_holder.enum_renov
        weights = {}
        for face in faces:
            value = int(face[layer])
            weights[value] = weights.get(value, 0.0) + face.calc_area()
        if not weights:
            return data_holder.enum_renov
        winner = max(weights.items(), key=lambda item: item[1])[0]
        return bim.STATUS_TO_CODE.get(bim.status_from_index(winner), "N")

    @classmethod
    def _split_runs(cls, faces, matrix_world, split_layer):
        """Faces grouped into separate walls: first by facing, then by the
        barrier edges.

        A wall that spans several storeys is one coplanar run and comes out as a
        single element, which is wrong for quantities and for anything that
        works per floor. Marking the slab lines as barriers cuts it: faces are
        connected only through edges that are NOT flagged, so each storey ends
        up its own island."""
        runs = []
        for run in cls._split_by_direction(faces, matrix_world):
            if split_layer is None:
                runs.append(run)
                continue
            runs.extend(cls._split_by_barrier(run, split_layer))
        return runs

    @classmethod
    def _split_by_barrier(cls, faces, split_layer):
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
                        continue  # a barrier: the wall stops here
                    for linked in edge.link_faces:
                        if linked in remaining and linked in members:
                            remaining.discard(linked)
                            island.append(linked)
                            stack.append(linked)
            islands.append(island)
        return islands

    @classmethod
    def _split_by_direction(cls, faces, matrix_world, tolerance=0.9):
        """Split a coplanar group whose faces do not share a facing.

        group_coplanar_faces keys on the rounded normal, so two faces at a
        slight angle — a wall run that is not perfectly axis-aligned, or one
        rounded across a precision boundary — can land in the same group facing
        different ways. Their area-weighted normal then partly cancels, the
        tangent comes out wrong and the wall is dropped. Splitting by actual
        facing keeps each run measurable."""
        rotation = matrix_world.to_3x3()
        runs = []
        for face in faces:
            normal = rotation @ face.normal
            normal.z = 0.0
            if normal.length < TOL:
                continue
            normal.normalize()
            for run in runs:
                if run["normal"].dot(normal) > tolerance:
                    run["faces"].append(face)
                    break
            else:
                runs.append({"normal": normal, "faces": [face]})
        return [run["faces"] for run in runs]

    @classmethod
    def _measure_wall_group(cls, faces, matrix_world):
        """Height and length of a run of coplanar wall faces.

        Measured from the extents of the group, never from a dissolved face.
        Dissolving was the bug: leaving the door face unmarked turns the marked
        faces into a ring around a hole, and bmesh cannot represent a face with
        an interior loop — the result was the mangled geometry, and marking the
        door face too made it work only because the hole disappeared.

        Height is the Z range. Length is the span along the wall's own tangent,
        so a hole in the middle changes nothing. The origin sits at the low end
        of that span, at the lowest Z."""
        rotation = matrix_world.to_3x3()
        normal = mathutils.Vector((0.0, 0.0, 0.0))
        for face in faces:
            normal += (rotation @ face.normal) * face.calc_area()
        normal.z = 0.0
        if normal.length < TOL:
            # Area-weighted normals that cancel out. Two coplanar faces pointing
            # opposite ways, or horizontal faces marked as wall, both land here —
            # and the wall used to vanish without a word.
            print(
                "[RefOps] Grupo de parede sem direção horizontal (faces opostas "
                "ou horizontais marcadas como parede) — ignorado."
            )
            return None
        normal.normalize()
        tangent = mathutils.Vector((normal.y, -normal.x, 0.0))

        points = [matrix_world @ vert.co for face in faces for vert in face.verts]
        if not points:
            return None

        z_min = min(point.z for point in points)
        height = max(point.z for point in points) - z_min
        projections = [point.x * tangent.x + point.y * tangent.y for point in points]
        low, high = min(projections), max(projections)
        length = high - low
        if length < TOL or height < TOL:
            print(
                f"[RefOps] Parede descartada: comprimento {length:.4f}, "
                f"altura {height:.4f}."
            )
            return None

        # Rebuild the origin from the tangent coordinate: the point at `low`
        # along the tangent, on the wall plane.
        reference = points[projections.index(low)]
        lateral = reference.x * normal.x + reference.y * normal.y
        origin = mathutils.Vector(
            (
                tangent.x * low + normal.x * lateral,
                tangent.y * low + normal.y * lateral,
                z_min,
            )
        )
        return height, length, tangent, normal, origin

    @classmethod
    def _find_clip_planes(cls, wall_face, matrix_world, wall_face_set):
        """Currently unused — kept for the section-plane feature. Returns
        world-space planes."""
        up = mathutils.Vector((0.0, 0.0, 1.0))
        rot = matrix_world.to_3x3()
        planes = []
        for edge in wall_face.edges:
            for linked in edge.link_faces:
                if linked == wall_face or linked in wall_face_set:
                    continue
                linked_normal = (rot @ linked.normal).normalized()
                if (linked_normal - up).length < 1e-3:
                    continue
                top_vert = max(
                    (matrix_world @ v.co for v in edge.verts), key=lambda v: v.z
                )
                planes.append({"point": top_vert, "normal": linked_normal})
        return planes
