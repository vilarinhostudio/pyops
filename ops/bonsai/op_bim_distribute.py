"""Distribute BIM elements over marked vertices.

Replaces the opening operator's face mode entirely. Sizing an opening from a
drawn boundary meant rebuilding, by hand, what Bonsai already models
parametrically — it is more practical to configure the door or window once in
Bonsai and scatter it over points.

    vertex bool attribute -> one element per marked vertex
    -> placement from the point and its attributes
    -> optionally cut into the wall it sits on

Three outputs on ONE enum, not two enums crossed: half the combinations were
never real. A door that is not an IFC element cannot fill anything, so
"Blender" and "Door / Window" together was a state the UI could reach and the
code had no answer for.

* FILLING keeps Z up always, positions itself against the wall plane and can
  void the wall it lands on.
* ELEMENT is any other IFC occurrence — furniture, equipment — and only needs
  to know which way is up.
* BLENDER places exactly like ELEMENT and stops there: a plain copy, no IFC.
"""

import math

import bmesh
import bpy
import mathutils

import refops_api

bim = refops_api.bim_helpers
prof = refops_api.bim_profiles

TOL = bim.TOL


class BonsaiDistributeOnPoints:
    id = "BIM_DISTRIBUTE_ON_POINTS"
    label = "Bonsai Distribute on Points"
    icon = "PARTICLES"
    data_type = "MESH"
    execution_mode = "OBJECT"

    props_definition = {
        "enum_output": (
            "Enum",
            {
                "name": "Distribute As",
                "items": [
                    (
                        "FILLING",
                        "IFC Door / Window",
                        "Real occurrence that fills an opening in a host",
                    ),
                    (
                        "ELEMENT",
                        "IFC Component",
                        "Real occurrence placed freely, counted in schedules",
                    ),
                    (
                        "ANNOTATION",
                        "IFC Annotation",
                        "Copy of an annotation, assigned to a drawing",
                    ),
                    ("BLENDER", "Blender Objects", "Plain objects, no IFC data"),
                ],
                "default": "FILLING",
            },
        ),
        "target_collection": (
            "Collection",
            {
                "name": "Target Collection",
                "description": "Where the copies land; the scene collection when empty",
                "show_if": ("enum_output", {"BLENDER"}),
            },
        ),
        "enum_copy": (
            "Enum",
            {
                "name": "Copy",
                "items": [
                    ("LINKED", "Linked Data", "One mesh shared by every copy (Alt+D)"),
                    ("FULL", "Full Copy", "Independent mesh per copy (Ctrl+D)"),
                ],
                "default": "LINKED",
                "show_if": ("enum_output", {"BLENDER"}),
            },
        ),
        "sep0": ("Separator", {}),
        "lbl_points": ("Label", {"text": "Points", "icon": "VERTEXSEL"}),
        "point_attr": (
            "String",
            {"name": "Point Attr (vert bool)", "search": "attribute"},
        ),
        "sep1": ("Separator", {}),
        "lbl_source": (
            "Label",
            {"text": "What to Distribute", "icon": "OUTLINER_OB_GROUP_INSTANCE"},
        ),
        "enum_source": (
            "Enum",
            {
                "name": "Source",
                "items": [
                    ("OBJECT", "Object", "One element, repeated"),
                    ("COLLECTION", "Collection", "Picked per point by weight"),
                ],
                "default": "OBJECT",
            },
        ),
        "source_object": (
            "Object",
            {"name": "Source Element", "show_if": ("enum_source", {"OBJECT"})},
        ),
        "source_collection": (
            "Collection",
            {"name": "Source Collection", "show_if": ("enum_source", {"COLLECTION"})},
        ),
        "weight_attr": (
            "String",
            {
                "name": "Weight Attr (vert float)",
                "search": "attribute",
                "show_if": ("enum_source", {"COLLECTION"}),
            },
        ),
        "sep2": ("Separator", {}),
        # --- door / window ---
        "lbl_dw": (
            "Label",
            {
                "text": "Placement",
                "icon": "MOD_BUILD",
                "show_if": ("enum_output", {"FILLING"}),
            },
        ),
        "enum_align_mode": (
            "Enum",
            {
                "name": "Align From",
                "items": [
                    (
                        "ORIGIN",
                        "Object Origin",
                        "The element's own origin lands on the point",
                    ),
                    (
                        "BOUNDING_BOX",
                        "Bounding Box",
                        "The cardinal corner lands on the point",
                    ),
                ],
                "default": "ORIGIN",
                "show_if": ("enum_output", {"FILLING"}),
            },
        ),
        "align_attr": (
            "String",
            {
                "name": "Align Attr (vert int 1-9)",
                "search": "attribute",
                "description": (
                    "Numpad layout: which point of the element's bounding box "
                    "is placed on the vertex. 2 its bottom centre, 8 its top"
                ),
                "show_if": [
                    ("enum_output", {"FILLING"}),
                    ("enum_align_mode", {"BOUNDING_BOX"}),
                ],
            },
        ),
        "face_align_attr": (
            "String",
            {
                "name": "Face Align Attr (face bool) — optional",
                "search": "attribute",
                "description": (
                    "Rarely needed: the wall plane comes from the horizontal "
                    "edges through the point. Use only where those cannot "
                    "settle it, such as a point shared by two walls"
                ),
                "show_if": ("enum_output", {"FILLING"}),
            },
        ),
        "flip_attr": (
            "String",
            {
                "name": "Flip Attr (vert bool)",
                "search": "attribute",
                "description": (
                    "Off, the element faces the way the reference face's "
                    "normal points; on, the other way"
                ),
                "show_if": ("enum_output", {"FILLING"}),
            },
        ),
        "apply_to_wall": (
            "Bool",
            {
                "name": "Cut Into Host",
                "default": True,
                "show_if": ("enum_output", {"FILLING"}),
            },
        ),
        "host_class": (
            "String",
            {
                "name": "Host Class",
                "default": "IfcWall",
                "show_if": [
                    ("enum_output", {"FILLING"}),
                    ("apply_to_wall", {True}),
                ],
            },
        ),
        # --- free elements ---
        "lbl_free": (
            "Label",
            {
                "text": "Orientation",
                "icon": "ORIENTATION_GIMBAL",
                "show_if": ("enum_output", {"ELEMENT", "BLENDER", "ANNOTATION"}),
            },
        ),
        "enum_orientation": (
            "Enum",
            {
                "name": "Up / Rotation",
                "items": [
                    ("Z_UP", "Z Up", "World up, no rotation"),
                    (
                        "ANGLE",
                        "Z Up + Angle Attr",
                        "World up, turned by a float attribute",
                    ),
                    ("NORMAL", "Vertex Normal", "Average of the faces at the point"),
                    ("VECTOR", "Vector Attr", "Float-vector attribute on the vertex"),
                ],
                "default": "Z_UP",
                "show_if": ("enum_output", {"ELEMENT", "BLENDER", "ANNOTATION"}),
            },
        ),
        "angle_attr": (
            "String",
            {
                "name": "Angle Attr (vert float, degrees)",
                "search": "attribute",
                "description": (
                    "Rotation about the up axis, in degrees; for an annotation, "
                    "about the drawing's view normal"
                ),
                "show_if": [
                    ("enum_output", {"ELEMENT", "BLENDER", "ANNOTATION"}),
                    ("enum_orientation", {"ANGLE"}),
                ],
            },
        ),
        "vector_attr": (
            "String",
            {
                "name": "Vector Attr (vert vector)",
                "search": "attribute",
                "show_if": [
                    ("enum_output", {"ELEMENT", "BLENDER"}),
                    ("enum_orientation", {"VECTOR"}),
                ],
            },
        ),
        "sep3": ("Separator", {}),
        "drawing_pointer": (
            "Object",
            {
                "name": "Drawing (camera object)",
                "show_if": ("enum_output", {"ANNOTATION"}),
            },
        ),
        "enum_storey": (
            "Enum",
            {
                "name": "Storey From",
                "items": [
                    ("POINTER", "Object", "One storey for every point"),
                    ("ATTRIBUTE", "Attribute", "An index per point, 0 at the ground"),
                ],
                "default": "POINTER",
                "show_if": ("enum_output", {"FILLING", "ELEMENT"}),
            },
        ),
        "story_pointer": (
            "Object",
            {
                "name": "Building Story",
                "show_if": [
                    ("enum_output", {"FILLING", "ELEMENT"}),
                    ("enum_storey", {"POINTER"}),
                ],
            },
        ),
        "storey_attr": (
            "String",
            {
                "name": "Storey Attr (vert int)",
                "search": "attribute",
                "description": "Index into the storeys ordered by height, 0 at the ground",
                "show_if": [
                    ("enum_output", {"FILLING", "ELEMENT"}),
                    ("enum_storey", {"ATTRIBUTE"}),
                ],
            },
        ),
        "precision": ("Int", {"name": "Rounding Decimals", "default": 3}),
    }

    # --- ENTRY POINT ---

    @classmethod
    def execute(cls, container_obj, data_holder):
        parent_obj = bpy.data.objects.get(
            getattr(container_obj, "proc_parent_name", "")
        )
        if not parent_obj or parent_obj.type != "MESH":
            return False

        # Bonsai and an open project are demanded by the BIM path ONLY.
        # Scattering plain objects is pure Blender, and asking for an IFC file
        # to do it is what kept that half of the operator from ever running.
        to_ifc = data_holder.enum_output in {"FILLING", "ELEMENT", "ANNOTATION"}
        ifc_file = None
        if to_ifc:
            if not bim.ensure():
                print("[RefOps] Bonsai indisponível:")
                print(bim.IMPORT_ERROR)
                return False
            ifc_file = bim.get_file()
            if not ifc_file:
                print("[RefOps] Nenhum projeto IFC ativo.")
                return False

        container_obj.display_type = "WIRE"
        container_obj.hide_render = True
        container_obj.name = f"POINTS_CONTAINER_{parent_obj.name}"

        key = bim.family_key(container_obj, data_holder.point_attr)
        storey = None
        drawing = None
        if data_holder.enum_output == "ANNOTATION":
            drawing = cls._drawing_target(data_holder)
            if drawing is None:
                return False
        if to_ifc:
            storey = bim.entity(data_holder.story_pointer)
            # IfcAnnotation is not an IfcElement; both classes are swept so
            # switching the output between runs does not strand the old copies.
            bim.cleanup_family(ifc_file, ("IfcElement", "IfcAnnotation"), key)
        else:
            # A copy carries no pset, so the key written onto the object is the
            # only thing the next refresh can collect it by.
            bim.cleanup_tagged_objects(key)

        source = container_obj
        bm = bmesh.new()
        bm.from_mesh(source.data)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        layers = cls._layers(bm, data_holder)
        if layers is None:
            bm.free()
            return False

        points = [vert for vert in bm.verts if vert[layers["select"]]]
        if not points:
            bm.free()
            print("[RefOps] Nenhum vértice marcado.")
            return False

        weight_range = None
        if layers["weight"]:
            values = [vert[layers["weight"]] for vert in points]
            weight_range = (min(values), max(values))

        created = []
        for vert in points:
            if drawing is not None:
                placed = cls._place_annotation(
                    ifc_file,
                    vert=vert,
                    layers=layers,
                    weight_range=weight_range,
                    source=source,
                    data_holder=data_holder,
                    drawing=drawing,
                    key=key,
                )
            elif to_ifc:
                placed = cls._place(
                    ifc_file,
                    vert=vert,
                    layers=layers,
                    weight_range=weight_range,
                    source=source,
                    data_holder=data_holder,
                    storey=storey,
                    key=key,
                )
            else:
                placed = cls._place_plain(
                    vert=vert,
                    layers=layers,
                    weight_range=weight_range,
                    source=source,
                    data_holder=data_holder,
                    key=key,
                )
            if placed:
                created.append(placed)

        bm.free()
        print(f"[RefOps] {len(created)} de {len(points)} pontos preenchidos.")
        return bool(created)

    # --- ATTRIBUTES ---

    @classmethod
    def _layers(cls, bm, data_holder):
        select = bm.verts.layers.bool.get(data_holder.point_attr)
        if not select:
            print(f"[RefOps] Atributo '{data_holder.point_attr}' não encontrado.")
            return None

        def vert_int(name):
            return bm.verts.layers.int.get(name) if name else None

        def vert_bool(name):
            return bm.verts.layers.bool.get(name) if name else None

        def vert_float(name):
            return bm.verts.layers.float.get(name) if name else None

        return {
            "select": select,
            "align": vert_int(data_holder.align_attr),
            "flip": vert_bool(data_holder.flip_attr),
            "weight": vert_float(data_holder.weight_attr),
            "angle": vert_float(data_holder.angle_attr),
            "storey": vert_int(data_holder.storey_attr),
            "vector": (
                bm.verts.layers.float_vector.get(data_holder.vector_attr)
                if data_holder.vector_attr
                else None
            ),
            "face_align": (
                bm.faces.layers.bool.get(data_holder.face_align_attr)
                if data_holder.face_align_attr
                else None
            ),
        }

    # --- SOURCE ---

    @classmethod
    def _pick_source(cls, vert, layers, weight_range, data_holder):
        if data_holder.enum_source == "OBJECT":
            return data_holder.source_object
        objects = prof.ordered_objects(data_holder.source_collection)
        if not objects:
            return None
        if not layers["weight"] or not weight_range:
            return objects[0]
        low, high = weight_range
        index = prof.weight_to_index(vert[layers["weight"]], low, high, len(objects))
        return objects[index] if index is not None else None

    # --- PLACEMENT ---

    @classmethod
    def _place(
        cls, ifc_file, vert, layers, weight_range, source, data_holder, storey, key
    ):
        template = cls._pick_source(vert, layers, weight_range, data_holder)
        if template is None:
            print("[RefOps] Nenhum objeto de origem para este ponto.")
            return None

        entity = bim.entity(template)
        if entity is None:
            print(f"[RefOps] '{template.name}' não é um elemento IFC.")
            return None

        # The host comes FIRST now: the placement is built against the wall's
        # own face planes, so it has to be known before the matrix exists. The
        # lookup uses the painted point rather than the element's final origin —
        # the cardinal shift can carry a door past the end of its wall, and the
        # point is what the user actually marked.
        host = None
        if data_holder.enum_output == "FILLING" and data_holder.apply_to_wall:
            host = cls._find_host(
                ifc_file, source.matrix_world @ vert.co, data_holder.host_class
            )
            if host is None:
                print("[RefOps] Nenhum hospedeiro encontrado para este ponto.")

        matrix, cardinal_axis = cls._matrix_for(
            vert, layers, source, data_holder, host
        )
        if matrix is None:
            return None

        # A real occurrence of the type, so it counts in schedules — not a
        # Blender duplicate wearing a class.
        element_type, _ifc_class = bim.filling_type_of(entity)
        obj, element = bim.occurrence_from_type(element_type, matrix)
        if element is None:
            print(f"[RefOps] Não consegui instanciar o tipo de '{template.name}'.")
            return None
        obj.matrix_world = matrix

        if data_holder.enum_storey == "ATTRIBUTE" and layers["storey"]:
            storey = bim.storey_by_index(int(vert[layers["storey"]]), ifc_file)
        bim.assign_container(ifc_file, element, storey)
        bim.tag_family(ifc_file, element, key)

        # Body first, THEN the cardinal shift: the numpad anchors the real
        # bounding box, and before the rebuild that box is the type's, not the
        # occurrence's. Both stay ahead of the void — Bonsai sizes the opening
        # from the filling as it stands, so a door moved afterwards leaves its
        # void behind at the old place.
        bim.regenerate_parametric(obj)

        # Which jamb the leaf hangs on is the TYPE's business — Bonsai exposes
        # it as a type property only, so changing it swings every door of that
        # type. The old per-point "direction" attribute could not do better:
        # negating one horizontal axis mirrored the placement instead of
        # re-hanging the door, and the mirror does not survive an IFC export.

        # Gated on FILLING, not on the align mode alone: the field is only
        # drawn for a filling, so a value left over from an earlier setting
        # would otherwise shift a component that never asked to be aligned.
        if (
            data_holder.enum_output == "FILLING"
            and data_holder.enum_align_mode == "BOUNDING_BOX"
        ):
            cardinal = int(vert[layers["align"]]) if layers["align"] else 5
            cls._apply_cardinal(obj, cardinal, cardinal_axis)

        if data_holder.enum_output == "FILLING" and data_holder.apply_to_wall:
            if host is not None:
                # The filling cuts its own void: Bonsai sizes the opening from
                # the element as it stands.
                # The host's mesh is rebuilt when the void lands, and the
                # rebuild arrives bare: cut material, cutter and inherited
                # material all go with it. Captured here, restored after.
                host_finish = bim.capture_finish(bim.object_for(host))

                # preserve_placement: the door is already seated on the face it
                # opens through. Letting Bonsai re-snap it overwrites the whole
                # matrix with the host's, which is what made the flip do nothing
                # and tied the cardinal to the wall's alignment.
                opening = bim.opening_from_object(host, obj, preserve_placement=True)
                if opening is None:
                    print("[RefOps] O vazio não foi criado.")
                else:
                    # Tagged like everything else this run produced. Untagged,
                    # the cleanup could not see it and every refresh piled
                    # another void into the file.
                    bim.tag_family(ifc_file, opening, key)
                    bim.restore_finish(bim.object_for(host), host_finish)

        return element

    @classmethod
    def _drawing_target(cls, data_holder):
        """The drawing entity behind the pointed camera object, or None."""
        drawing = bim.entity(data_holder.drawing_pointer)
        if (
            drawing is None
            or not drawing.is_a("IfcAnnotation")
            or drawing.ObjectType != "DRAWING"
        ):
            print("[RefOps] 'Drawing' deve apontar para a câmera de um drawing do Bonsai.")
            return None
        if not bim.tool.Drawing.get_drawing_group(drawing):
            print("[RefOps] O drawing não tem grupo — não há onde atribuir a anotação.")
            return None
        return drawing

    @classmethod
    def _place_annotation(
        cls, ifc_file, vert, layers, weight_range, source, data_holder, drawing, key
    ):
        """One copy of an annotation, assigned to the drawing and laid in its plane.

        Bonsai's own Copy to Drawing is not called: it skips annotations already
        in the target drawing, and a source living there is a normal case here.
        The same steps are repeated instead."""
        template = cls._pick_source(vert, layers, weight_range, data_holder)
        entity = bim.entity(template) if template is not None else None
        if entity is None or not entity.is_a("IfcAnnotation"):
            print("[RefOps] A fonte deste ponto não é um IfcAnnotation.")
            return None

        camera = bim.object_for(drawing)
        group = bim.tool.Drawing.get_drawing_group(drawing)
        if camera is None:
            print("[RefOps] O drawing não tem objeto de câmera na cena.")
            return None

        try:
            old_to_new, _ = bim.tool.Geometry.duplicate_ifc_objects([template])
            copies = [e for new in old_to_new.values() for e in new]
        except Exception as error:
            print(f"[RefOps] Falha ao duplicar a anotação (API do Bonsai mudou?): {error}")
            return None
        if not copies:
            return None
        element = copies[0]

        old_group = bim.tool.Drawing.get_drawing_group(element)
        if old_group:
            bim.tool.Ifc.run("group.unassign_group", group=old_group, products=[element])
        bim.tool.Ifc.run("group.assign_group", group=group, products=[element])

        # Spin about the drawing's view normal, not world Z: in a section the
        # plane is vertical and a world-Z turn would tilt the annotation out of it.
        position = source.matrix_world @ vert.co
        normal = camera.matrix_world.to_3x3().col[2].normalized()
        angle = 0.0
        if data_holder.enum_orientation == "ANGLE" and layers["angle"]:
            angle = math.radians(float(vert[layers["angle"]]))
        elif data_holder.enum_orientation in {"NORMAL", "VECTOR"}:
            print("[RefOps] Anotação só usa Z_UP e ANGLE; orientação ignorada.")
        obj = bim.object_for(element)
        obj.matrix_world = mathutils.Matrix.Translation(position) @ (
            mathutils.Matrix.Rotation(angle, 4, normal)
        )
        bim.tool.Drawing.ensure_annotation_in_drawing_plane(obj, camera)
        bim.tool.Collector.assign(obj, should_clean_users_collection=True)
        bim.tag_family(ifc_file, element, key)
        return element

    @classmethod
    def _matrix_for(cls, vert, layers, source, data_holder, host=None):
        """(placement, cardinal axis) for one point.

        The axis is None for anything that is not a filling — only a filling is
        cardinal-aligned."""
        matrix_world = source.matrix_world
        position = matrix_world @ vert.co

        # Only a filling reads the wall plane. Everything else — IFC component
        # or plain copy — is placed free, which is why the Blender path can
        # share this function instead of carrying its own.
        if data_holder.enum_output == "FILLING":
            return cls._wall_matrix(vert, layers, matrix_world, position, host)
        return (
            cls._free_matrix(vert, layers, matrix_world, position, data_holder),
            None,
        )

    @classmethod
    def _wall_matrix(cls, vert, layers, matrix_world, position, host):
        """Door or window placement: (matrix, cardinal axis).

        Local Z is world up, always — these elements stand. Which way the
        element faces comes from the reference mesh: the normal of the wall
        plane at the point, taken from the faces marked for alignment when
        there are any and from the vertical faces around the vertex otherwise.
        Averaging in a floor face is what used to tilt the frame.

        Bonsai's convention for a filling is that its origin sits ON a face of
        the host and its local +Y points INTO the wall, so the element faces the
        way that face does. Unflipped, that face is the one the reference normal
        points out of; flipped, the other one."""
        normal = cls._wall_normal(vert, layers, matrix_world)
        if normal is None:
            return None, None

        # Outward: the way the element faces and opens. The flip is the whole
        # of the mechanic — the hinge side belongs to the type.
        outward = normal.copy()
        if layers["flip"] and vert[layers["flip"]]:
            outward = -outward

        # Local Y is INTO the wall, so it is the opposite of the facing. X is
        # then Y x Z, which keeps the frame right-handed: IfcAxis2Placement3D
        # stores only Z and X and derives Y from them, so a mirrored frame
        # lives in Blender and exports as an element facing the other way.
        through = -outward
        across = through.cross(mathutils.Vector((0.0, 0.0, 1.0)))

        # Rightwards seen from the reference normal's side — that is where the
        # numpad is read from, and it does NOT follow the flip: the number names
        # a place on the WALL, not on the element.
        cardinal_axis = mathutils.Vector((-normal.y, normal.x, 0.0))

        origin = cls._seat_on_host(host, position, outward)

        matrix = mathutils.Matrix(
            (
                (across.x, through.x, 0.0, origin.x),
                (across.y, through.y, 0.0, origin.y),
                (0.0, 0.0, 1.0, origin.z),
                (0.0, 0.0, 0.0, 1.0),
            )
        )
        return matrix, cardinal_axis

    @classmethod
    def _seat_on_host(cls, host, position, outward):
        """Slide the point across the wall onto the face it opens through.

        The two faces are the host's own bounding box in its local Y — the body
        is built spanning exactly from one to the other, so reading them off the
        object needs no unit conversion and survives an alignment that moved the
        body off the reference line. Without a host the point stands as it is:
        there is no wall to seat against, and the reference plane is the best
        guess left."""
        host_obj = bim.object_for(host) if host is not None else None
        if host_obj is None or host_obj.data is None:
            return position
        try:
            local = host_obj.matrix_world.inverted() @ position
        except ValueError:
            return position  # singular matrix: a degenerate host
        ys = [corner[1] for corner in host_obj.bound_box]
        # +local Y of a wall is its reference normal, so the face that opens
        # towards `outward` is the far one when the two agree.
        forward = host_obj.matrix_world.to_3x3().col[1]
        local.y = max(ys) if forward.dot(outward) > 0 else min(ys)
        return host_obj.matrix_world @ local

    @classmethod
    def _wall_normal(cls, vert, layers, matrix_world):
        """Which way the wall faces at this point, taken from the EDGES.

        A door point sits on the line where the wall meets the floor, so the
        faces around it include the floor and averaging normals is hopeless —
        and marking faces by hand does not scale once two doors share a wall and
        a third is on another.

        The edges do carry the answer. The horizontal edges through the point
        run ALONG the wall, so the wall plane is perpendicular to them; a window
        placed mid-face works the same way, since its loop crossing has
        horizontal edges too. Which side is outside then comes from the vertical
        faces touching those same edges — only those, so a neighbouring wall
        cannot contribute.

        A face attribute can still override this, for the cases geometry cannot
        settle on its own."""
        rotation = matrix_world.to_3x3()

        if layers["face_align"] is not None:
            marked = [face for face in vert.link_faces if face[layers["face_align"]]]
            if marked:
                normal = mathutils.Vector((0.0, 0.0, 0.0))
                for face in marked:
                    normal += (rotation @ face.normal) * face.calc_area()
                normal.z = 0.0
                if normal.length > TOL:
                    return normal.normalized()

        runs = []
        for edge in vert.link_edges:
            a, b = (matrix_world @ v.co for v in edge.verts)
            direction = mathutils.Vector((b.x - a.x, b.y - a.y, 0.0))
            span = direction.length
            if span < TOL or abs(b.z - a.z) > span:
                continue  # vertical, or too steep to describe a wall run
            direction /= span
            for run in runs:
                if abs(run["direction"].dot(direction)) > 0.99:
                    run["length"] += span
                    run["edges"].append(edge)
                    break
            else:
                runs.append({"direction": direction, "length": span, "edges": [edge]})

        if not runs:
            print("[RefOps] Ponto sem aresta horizontal — não dá para achar a parede.")
            return None

        run = max(runs, key=lambda item: item["length"])
        tangent = run["direction"]
        normal = mathutils.Vector((tangent.y, -tangent.x, 0.0)).normalized()

        # Sign from the vertical faces on those edges only.
        reference = mathutils.Vector((0.0, 0.0, 0.0))
        for edge in run["edges"]:
            for face in edge.link_faces:
                face_normal = rotation @ face.normal
                if abs(face_normal.normalized().z) < 0.5:
                    reference += face_normal * face.calc_area()
        if reference.length > TOL and reference.dot(normal) < 0:
            normal = -normal
        return normal

    @classmethod
    def _plain_copy(cls, template, matrix, data_holder):
        """A Blender duplicate, linked or full.

        Linked shares the mesh data-block — the Alt+D of a hundred chairs costs
        one mesh in the file. Full copies the data too, for when each one will
        be edited on its own."""
        obj = template.copy()
        if data_holder.enum_copy == "FULL" and template.data is not None:
            obj.data = template.data.copy()
        obj.name = f"{template.name}_copy"
        target = data_holder.target_collection or bpy.context.scene.collection
        target.objects.link(obj)
        obj.matrix_world = matrix
        return obj

    @classmethod
    def _place_plain(cls, vert, layers, weight_range, source, data_holder, key):
        """One plain Blender duplicate on the point.

        The placement maths is shared with the BIM path — a matrix does not care
        whether an occurrence or a copy lands on it. What is skipped is every
        IFC step: no type, no storey, no void cut into the host."""
        template = cls._pick_source(vert, layers, weight_range, data_holder)
        if template is None:
            print("[RefOps] Nenhum objeto de origem para este ponto.")
            return None

        matrix, _axis = cls._matrix_for(vert, layers, source, data_holder)
        if matrix is None:
            return None

        obj = cls._plain_copy(template, matrix, data_holder)
        # Tagged like the IFC elements are, so the next refresh removes these
        # copies instead of piling a new set on top of them.
        bim.tag_object(obj, key)
        return obj

    @classmethod
    def _apply_cardinal(cls, obj, cardinal, right):
        """Move the element so the numbered point of its bounding box lands on
        the marked vertex.

        The number names an ANCHOR on the box, and the box travels to the point:
        8 brings the top-centre down onto it, 2 the bottom-centre. That is why
        4 through 9 must lower the door rather than raise it.

        The distance comes from where the bounding box sits relative to the
        origin, not from half its size: a door authored with its origin at the
        bottom centre has min_z of zero, and moving by half the height would
        lift it off the floor.

        `right` is the wall's rightwards direction seen from the normal side,
        and it does NOT follow the flip. Measuring the box against the object's
        own axes instead made left and right trade places whenever the door was
        flipped, so only half the numpad ever landed where it was asked to.

        Only the horizontal axis along the wall and Z are touched. The axis
        through the wall is left alone: a filling belongs inside what it cuts."""
        if not 1 <= cardinal <= 9 or obj.data is None or right is None:
            return
        # The box corners are local and relative to the origin, so rotating them
        # without translating gives the extents around the point itself.
        rotation = obj.matrix_world.to_3x3()
        right = right.normalized()
        up = mathutils.Vector((0.0, 0.0, 1.0))
        corners = [rotation @ mathutils.Vector(corner) for corner in obj.bound_box]
        rights = [corner.dot(right) for corner in corners]
        ups = [corner.dot(up) for corner in corners]
        min_r, max_r = min(rights), max(rights)
        min_u, max_u = min(ups), max(ups)

        column = (cardinal - 1) % 3  # 0 left, 1 centre, 2 right
        row = (cardinal - 1) // 3  # 0 bottom, 1 middle, 2 top
        target_r = (min_r, (min_r + max_r) / 2.0, max_r)[column]
        target_u = (min_u, (min_u + max_u) / 2.0, max_u)[row]

        if abs(target_r) < TOL and abs(target_u) < TOL:
            return
        obj.matrix_world.translation -= right * target_r + up * target_u

    @classmethod
    def _free_matrix(cls, vert, layers, matrix_world, position, data_holder):
        """Placement for anything that is not a filling."""
        mode = data_holder.enum_orientation
        up = mathutils.Vector((0.0, 0.0, 1.0))

        if mode == "NORMAL":
            rotation = matrix_world.to_3x3()
            normal = mathutils.Vector((0.0, 0.0, 0.0))
            for face in vert.link_faces:
                normal += (rotation @ face.normal) * face.calc_area()
            if normal.length > TOL:
                up = normal.normalized()
        elif mode == "VECTOR" and layers["vector"]:
            raw = matrix_world.to_3x3() @ mathutils.Vector(
                tuple(vert[layers["vector"]])
            )
            if raw.length > TOL:
                up = raw.normalized()

        seed = mathutils.Vector((0.0, 1.0, 0.0))
        if abs(up.dot(seed)) > 0.999:
            seed = mathutils.Vector((1.0, 0.0, 0.0))
        x_axis = seed.cross(up).normalized()
        y_axis = up.cross(x_axis).normalized()

        if mode == "ANGLE" and layers["angle"]:
            # Degrees in the attribute: an angle painted by hand is read and
            # typed in degrees, and radians in the UI only invite mistakes.
            angle = math.radians(float(vert[layers["angle"]]))
            cosine, sine = math.cos(angle), math.sin(angle)
            x_axis, y_axis = (
                x_axis * cosine + y_axis * sine,
                y_axis * cosine - x_axis * sine,
            )

        return mathutils.Matrix(
            (
                (x_axis.x, y_axis.x, up.x, position.x),
                (x_axis.y, y_axis.y, up.y, position.y),
                (x_axis.z, y_axis.z, up.z, position.z),
                (0.0, 0.0, 0.0, 1.0),
            )
        )

    # --- HOST ---

    @classmethod
    def _find_host(cls, ifc_file, point, host_class):
        """Nearest element of the class whose bounding box contains the point.

        The pad follows the host's own thickness: a wall moved by its alignment
        leaves the reference plane outside a tight box."""
        best, best_distance = None, None
        try:
            candidates = ifc_file.by_type(host_class or "IfcWall")
        except Exception:
            print(f"[RefOps] '{host_class}' não existe no schema.")
            return None

        for element in candidates:
            obj = bim.object_for(element)
            if not obj or not obj.data:
                continue
            local = obj.matrix_world.inverted() @ point
            corners = [mathutils.Vector(corner) for corner in obj.bound_box]
            low = mathutils.Vector(
                (
                    min(c.x for c in corners),
                    min(c.y for c in corners),
                    min(c.z for c in corners),
                )
            )
            high = mathutils.Vector(
                (
                    max(c.x for c in corners),
                    max(c.y for c in corners),
                    max(c.z for c in corners),
                )
            )
            signature = bim.layer_set_signature(element)
            thickness = sum(v for v, _m in signature) if signature else 0.0
            pad = max(0.05, thickness)
            if not all(low[i] - pad <= local[i] <= high[i] + pad for i in range(3)):
                continue
            distance = (obj.matrix_world.translation - point).length
            if best_distance is None or distance < best_distance:
                best, best_distance = element, distance
        return best
