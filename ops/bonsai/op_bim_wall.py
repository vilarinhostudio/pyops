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
ALIGN_MAP = {0: "INTERIOR", 1: "CENTER", 2: "EXTERIOR"}


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
        "lbl_filter": ("Label", {"text": "Principal", "icon": "RESTRICT_SELECT_OFF"}),
        "bool_attr": ("String", {"name": "Wall Select Attr", "search": "attribute"}),
        "align_attr": ("String", {"name": "Align Attr (int)", "search": "attribute"}),
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
        "sep1": ("Separator", {}),
        "lbl_type": ("Label", {"text": "Type Definition", "icon": "MOD_BUILD"}),
        "enum_wall_mode": (
            "Enum",
            {
                "name": "Type Mode",
                "items": [
                    ("C", "Object Type (Pointer)", "Use wall type object pointer"),
                    ("W", "Collection (Weighted)", "Pick a type per element by weight"),
                ],
                "default": "C",
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
        "do_trim": ("Bool", {"name": "Trim Corners", "default": True}),
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
        container_obj.name = f"WALL_CONTAINER_{parent_obj.name}"

        key = bim.family_key(container_obj, data_holder.bool_attr)
        bim.cleanup_family(ifc_file, "IfcWall", key)
        bim.cleanup_orphan_objects("WAL_")

        # Unconditional: guarding this behind `if data_holder.story_pointer`
        # left storey_pointer unbound whenever no pointer was set, and
        # _run_storey then raised on a name that never existed.
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

        wall_faces = [f for f in bm.faces if f[bool_layer]]
        if not wall_faces:
            bm.free()
            return False

        # Grouped by plane AND by the attributes that describe the element.
        # They were voted on per island before — area-weighted majority for the
        # alignment, mean for the type weight, lowest for the storey — which
        # meant one wall could be built from faces that disagreed. A wall now
        # follows the faces that actually carry its values, and a face left
        # unpainted falls to the default and becomes a wall of its own.
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

        def face_align(face):
            return int(face[align_layer]) if align_layer else 0

        def face_type_index(face):
            """The RESOLVED index, never the raw weight: two faces differing in
            the sixth decimal pick the same type and must stay one wall."""
            if data_holder.enum_wall_mode != "W" or not type_weight_layer:
                return None
            if not type_range or not type_objects:
                return None
            low, high = type_range
            return prof.weight_to_index(
                face[type_weight_layer], low, high, len(type_objects)
            )

        def face_storey(face):
            if data_holder.enum_storey != "ATTRIBUTE" or storey_layer is None:
                return None
            return int(face[storey_layer])

        groups = bim.group_coplanar_faces(
            wall_faces,
            source.matrix_world,
            data_holder.precision,
            extra_key=lambda face: (
                face_align(face),
                face_type_index(face),
                face_storey(face),
            ),
        )
        body_context = bim.get_body_context(ifc_file)

        island_of_obj = {}  # object name -> the faces it was built from
        align_groups = {}
        # Keyed by IFC entity, not by object: offset_walls and
        # join_walls_intersection can replace the Blender object, leaving any
        # reference taken before them pointing at something that is gone.
        created = []  # (IFC element, type index)
        types = bim.TypeIndexer()
        # A coplanar group can hold several disconnected runs of wall, and each
        # run is its own element.
        islands = [island for group in groups for island in bim.split_islands(group)]

        for group in islands:
            # Uniform across the group by construction — the values are part of
            # the grouping key, so the first face speaks for all of them.
            sample = group[0]
            align_value = face_align(sample)

            picked = cls._pick_type(type_objects, face_type_index(sample))
            wall_type = cls._resolve_wall_type(
                data_holder.enum_wall_mode,
                data_holder.wall_type_pointer,
                picked,
            )
            if not wall_type:
                continue

            material_set = bim.resolve_type_material(
                wall_type, "IfcMaterialLayerSet"
            )
            # A type without its layer set stops the element outright: the
            # thickness is READ from that set, so there is nothing to build.
            if material_set is None:
                continue

            thickness = bim.layer_set_thickness(material_set)
            if thickness <= 0:
                print(
                    f"[RefOps] O IfcMaterialLayerSet de "
                    f"'{wall_type.Name}' soma espessura zero — parede ignorada."
                )
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
                    key=key,
                )
                if obj:
                    island_of_obj[obj.name] = run
                    align_groups.setdefault(align_value, []).append(obj)
                    created.append((element, types.index(wall_type)))

        # Alignment first: trimming solves the corners against the final body
        # positions, so it has to run after the walls have been offset.
        cls._apply_alignment(align_groups)
        if data_holder.do_trim:
            cls._trim_corners(island_of_obj, source.matrix_world)

        # One void, cut into every element of the run. A Blender boolean would
        # be invisible to IFC; these are real openings, so they export and can
        # be edited from one source mesh.
        if data_holder.void_object:
            openings = bim.void_elements(
                data_holder.void_object, [element for element, _idx in created]
            )
            for opening in openings:
                bim.tag_family(ifc_file, opening, key)
            print(f"[RefOps] Vazio comum aplicado em {len(openings)} elementos.")

        # LAST: offset_walls and join_walls_intersection both rebuild the mesh
        # from the IFC representation, so anything written on the object earlier
        # in the loop is silently lost.
        for element, type_index in created:
            obj = bim.object_for(element)
            if not obj:
                # Bonsai removed it — a merge during the trim, most likely.
                print("[RefOps] Uma parede sumiu durante o trim.")
                continue
            bim.set_type_index(obj, type_index)

        bm.free()
        return bool(island_of_obj)

    # --- BUILD ---

    @classmethod
    def _build_wall(
        cls,
        ifc_file,
        faces,
        matrix_world,
        wall_type,
        thickness,
        storey,
        body_context,
        key,
    ):
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
    def _resolve_wall_type(cls, mode, type_pointer_obj, picked=None):
        """The type is always chosen, never invented.

        The old "Simple (Attribute)" mode built a type from a thickness
        attribute; it is gone because the layer set on the type is now the only
        source of thickness. Nothing here creates a type any more, so a file
        with no IfcWallType produces no walls — by design."""
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

        if not type_pointer_obj:
            print("[RefOps] Nenhum Wall Type Ref definido.")
            return None
        wall_type = bim.entity(type_pointer_obj)
        if wall_type is None or not wall_type.is_a("IfcWallType"):
            print(f"[RefOps] '{type_pointer_obj.name}' não é um IfcWallType.")
            return None
        return wall_type

    # --- FINISHING ---

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
            # The same normal _measure_wall_group built the wall from, so the
            # junction is classified against the body that actually exists.
            normal = rotation @ max(faces, key=lambda face: face.calc_area()).normal
            normal.z = 0.0
            if normal.length < TOL:
                continue
            normal.normalize()
            tangent = mathutils.Vector((normal.y, -normal.x, 0.0))
            points = [matrix_world @ vert.co for face in faces for vert in face.verts]
            projections = [
                point.x * tangent.x + point.y * tangent.y for point in points
            ]
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
            junctions.items(),
            key=lambda item: (0 if item[1] == "CORNER" else 1, item[0]),
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
    def _pick_type(cls, objects, index):
        """One type per element.

        The index is part of the grouping key, so every face of the element
        already resolved to the same type — there is nothing left to average."""
        if not objects:
            return None
        if index is None:
            return objects[0]
        return objects[index] if 0 <= index < len(objects) else None

    # --- GEOMETRY ---

    @classmethod
    def _run_storey(cls, ifc_file, faces, layer, pointer, data_holder):
        """The storey of one wall.

        The index is part of the grouping key, so every face here carries the
        same one — a wall that used to cross a floor line and be voted onto the
        lower storey is now two walls. The split attribute is still what cuts a
        wall on a line the storey attribute does not mark."""
        if data_holder.enum_storey != "ATTRIBUTE" or layer is None or not faces:
            return pointer
        return bim.storey_by_index(int(faces[0][layer]), ifc_file)

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

        Redundant at the default precision — the rounded normal is part of the
        grouping key, so a group already faces one way. It earns its place at a
        LOW precision, where the rounding bucket is wide enough to hold faces at
        a real angle to each other: the wall would then be built from the
        largest face's normal and the rest of the run would sit askew to it."""
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
        # The direction is ONE painted face's normal, never a blend of several.
        # The rounded normal is part of the grouping key, so the whole run faces
        # the same way already; the largest face is the one that speaks for it
        # when a low `precision` widens the bucket.
        normal = rotation @ max(faces, key=lambda face: face.calc_area()).normal
        normal.z = 0.0
        if normal.length < TOL:
            # A horizontal face marked as wall — it has no facing to build from,
            # and the wall used to vanish without a word.
            print(
                "[RefOps] Grupo de parede sem direção horizontal (face "
                "horizontal marcada como parede) — ignorado."
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
