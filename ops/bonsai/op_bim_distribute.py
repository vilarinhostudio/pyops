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
        "direction_attr": (
            "String",
            {
                "name": "Direction Attr (vert bool)",
                "search": "attribute",
                "show_if": ("enum_output", {"FILLING"}),
            },
        ),
        "flip_attr": (
            "String",
            {
                "name": "Flip Attr (vert bool)",
                "search": "attribute",
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
                "show_if": ("enum_output", {"ELEMENT", "BLENDER"}),
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
                "show_if": ("enum_output", {"ELEMENT", "BLENDER"}),
            },
        ),
        "angle_attr": (
            "String",
            {
                "name": "Angle Attr (vert float, degrees)",
                "search": "attribute",
                "description": "Rotation about the up axis, in degrees",
                "show_if": [
                    ("enum_output", {"ELEMENT", "BLENDER"}),
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
        to_ifc = data_holder.enum_output in {"FILLING", "ELEMENT"}
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
        if to_ifc:
            storey = bim.entity(data_holder.story_pointer)
            bim.cleanup_family(ifc_file, "IfcElement", key)
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
            if to_ifc:
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

        def vert_int(name):
            return bm.verts.layers.int.get(name) if name else None

        def vert_float(name):
            return bm.verts.layers.float.get(name) if name else None

        return {
            "select": select,
            "align": vert_int(data_holder.align_attr),
            "direction": vert_bool(data_holder.direction_attr),
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

        matrix, host_candidate = cls._matrix_for(
            vert, layers, source, data_holder, entity
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

        # Everything that changes the element happens BEFORE the void is cut.
        # Bonsai sizes the opening from the filling as it stands, so a door
        # moved or re-swung afterwards leaves its void behind at the old place —
        # which is exactly what the openings were doing.
        # Gated on FILLING, not on the align mode alone: the field is only
        # drawn for a filling, so a value left over from an earlier setting
        # would otherwise shift a component that never asked to be aligned.
        if (
            data_holder.enum_output == "FILLING"
            and data_holder.enum_align_mode == "BOUNDING_BOX"
        ):
            cardinal = int(vert[layers["align"]]) if layers["align"] else 5
            cls._apply_cardinal(obj, cardinal)

        # Build the body FIRST, then change the swing. The regeneration ends by
        # cancelling the door editor, and a cancel discards a pending direction
        # change — which is why flipping the geometry stuck and changing the
        # direction did not.
        bim.regenerate_parametric(obj)

        # Swing and hinge are handled by the placement above, not by the
        # parametric toggle: that one mirrored the leaf vertically and its
        # direction change was discarded by the editor cancel.

        if data_holder.enum_output == "FILLING" and data_holder.apply_to_wall:
            host = cls._find_host(
                ifc_file, obj.matrix_world.translation, data_holder.host_class
            )
            if host is None:
                print("[RefOps] Nenhum hospedeiro encontrado para este ponto.")
            else:
                # The filling cuts its own void: Bonsai sizes the opening from
                # the element and repositions it, which is the step that fixed
                # the placement when it was done by hand.
                # The host's mesh is rebuilt when the void lands, and the
                # rebuild arrives bare: cut material, cutter and inherited
                # material all go with it. Captured here, restored after.
                host_finish = bim.capture_finish(bim.object_for(host))

                opening = bim.opening_from_object(host, obj)
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
    def _matrix_for(cls, vert, layers, source, data_holder, entity):
        """(placement, host hint) for one point."""
        matrix_world = source.matrix_world
        position = matrix_world @ vert.co

        # Only a filling reads the wall plane. Everything else — IFC component
        # or plain copy — is placed free, which is why the Blender path can
        # share this function instead of carrying its own.
        if data_holder.enum_output == "FILLING":
            return cls._wall_matrix(vert, layers, matrix_world, position, entity), None
        return cls._free_matrix(
            vert, layers, matrix_world, position, data_holder
        ), None

    @classmethod
    def _wall_matrix(cls, vert, layers, matrix_world, position, entity):
        """Door or window placement.

        Local Z is world up, always — these elements stand. The horizontal
        direction comes from the wall plane at the point, taken from the faces
        marked for alignment when there are any and from the vertical faces
        around the vertex otherwise. Averaging in a floor face is what used to
        tilt the frame."""
        normal = cls._wall_normal(vert, layers, matrix_world)
        if normal is None:
            return None

        across = mathutils.Vector((normal.y, -normal.x, 0.0))
        through = -normal

        # Flip: the element faces the other side of the wall. A half turn about
        # the vertical, which is both horizontal axes at once — the parametric
        # toggle mirrored it top to bottom instead, which is not what "flip"
        # means for a door.
        if layers["flip"] and vert[layers["flip"]]:
            across = -across
            through = -through

        # Direction: the hinge moves to the other jamb. Negating only the axis
        # across the opening mirrors the leaf, and a mirrored door IS a door
        # hung on the other side. It is deliberately a left-handed frame.
        if layers["direction"] and vert[layers["direction"]]:
            across = -across

        # The element lands ON the point; the cardinal shift comes afterwards,
        # once its real bounding box is known. Bonsai has no alignment input of
        # its own, and the type's nominal size is not always the built size.
        centre = position

        return mathutils.Matrix(
            (
                (across.x, through.x, 0.0, centre.x),
                (across.y, through.y, 0.0, centre.y),
                (0.0, 0.0, 1.0, centre.z),
                (0.0, 0.0, 0.0, 1.0),
            )
        )

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

        matrix, _host = cls._matrix_for(vert, layers, source, data_holder, None)
        if matrix is None:
            return None

        obj = cls._plain_copy(template, matrix, data_holder)
        # Tagged like the IFC elements are, so the next refresh removes these
        # copies instead of piling a new set on top of them.
        bim.tag_object(obj, key)
        return obj

    @classmethod
    def _apply_cardinal(cls, obj, cardinal):
        """Move the element so the numbered point of its bounding box lands on
        the marked vertex.

        The number names an ANCHOR on the box, and the box travels to the point:
        8 brings the top-centre down onto it, 2 the bottom-centre. That is why
        4 through 9 must lower the door rather than raise it.

        The distance comes from where the bounding box sits relative to the
        origin, not from half its size: a door authored with its origin at the
        bottom centre has min_z of zero, and moving by half the height would
        lift it off the floor.

        Only X (across) and Z (up) are touched. Y runs through the wall, and a
        filling belongs inside the wall it cuts."""
        if not 1 <= cardinal <= 9 or obj.data is None:
            return
        corners = [mathutils.Vector(corner) for corner in obj.bound_box]
        min_x = min(c.x for c in corners)
        max_x = max(c.x for c in corners)
        min_z = min(c.z for c in corners)
        max_z = max(c.z for c in corners)

        column = (cardinal - 1) % 3  # 0 left, 1 centre, 2 right
        row = (cardinal - 1) // 3  # 0 bottom, 1 middle, 2 top
        target_x = (min_x, (min_x + max_x) / 2.0, max_x)[column]
        target_z = (min_z, (min_z + max_z) / 2.0, max_z)[row]

        if abs(target_x) < TOL and abs(target_z) < TOL:
            return
        rotation = obj.matrix_world.to_3x3()
        obj.matrix_world.translation -= (
            rotation.col[0].normalized() * target_x
            + rotation.col[2].normalized() * target_z
        )

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
