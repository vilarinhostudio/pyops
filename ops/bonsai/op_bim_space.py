"""IfcSpace per room, decomposed from the reference mesh.

    non-manifold mesh -> volumetric cells -> one IfcSpace each

The rooms come from the MESH, not from the generated walls. The reference mesh
is closed and non-manifold by construction — it is where the rooms were drawn —
while generated walls are trimmed, joined and occasionally imperfect. Deriving
spaces from them would inherit those imperfections.

Areas and volumes are GROSS: measured to the wall centrelines the mesh
describes. Net figures would need each face pulled back by half the thickness of
the wall that produced it, which lives in the type rather than in the mesh.

The decomposition is the same one the space syntax operator uses: around any
edge, faces sorted by angle bound the same pocket of space, so consecutive
pairs share a cell. Connected components of (face, side) are the rooms, and the
outside falls out because only an enclosed cell integrates a positive volume.

Two attributes go on the result so materials can be assigned downstream: an
integer identifying the room, and a boolean marking the floor faces.
"""

import bmesh
import bpy
import mathutils

import refops_api

bim = refops_api.bim_helpers

TOL = 1e-6


class BonsaiSpace:
    id = "BIM_SPACE"
    label = "Bonsai IFC Space"
    icon = "MESH_CUBE"
    data_type = "MESH"
    execution_mode = "OBJECT"

    props_definition = {
        "lbl_rooms": ("Label", {"text": "Rooms", "icon": "MESH_CUBE"}),
        "face_source": (
            "Enum",
            {
                "name": "Bounding Faces",
                "items": [
                    ("ALL_FACES", "All Faces", "Every face bounds a space"),
                    ("CONSTRUCTION", "Construction Elements", "Only the marked ones"),
                ],
                "default": "ALL_FACES",
            },
        ),
        "wall_attribute": (
            "String",
            {"name": "Walls", "search": "attribute",
             "show_if": ("face_source", {"CONSTRUCTION"})},
        ),
        "slab_attribute": (
            "String",
            {"name": "Slabs", "search": "attribute",
             "show_if": ("face_source", {"CONSTRUCTION"})},
        ),
        "roof_attribute": (
            "String",
            {"name": "Roofs", "search": "attribute",
             "show_if": ("face_source", {"CONSTRUCTION"})},
        ),
        "extra_attribute": (
            "String",
            {"name": "Other Elements", "search": "attribute",
             "show_if": ("face_source", {"CONSTRUCTION"})},
        ),
        "min_volume": (
            "Volume",
            {
                "name": "Minimum Volume",
                "default": 1.0,
                "min": 0.0,
                "description": "Cells below this are slivers, not rooms",
            },
        ),
        "room_id_attribute": (
            "String",
            {
                "name": "Room Id Attr (face int)",
                "search": "attribute",
                "description": "Optional: names each room; blank numbers them by volume",
            },
        ),
        "sep1": ("Separator", {}),
        "lbl_output": ("Label", {"text": "Output", "icon": "OUTLINER_OB_GROUP_INSTANCE"}),
        "name_prefix": ("String", {"name": "Name Prefix", "default": "Room"}),
        "room_index_attribute": (
            "String",
            {
                "name": "Room Index Attr",
                "default": "room_index",
                "description": "Integer written per face: which room it belongs to",
            },
        ),
        "floor_attribute": (
            "String",
            {
                "name": "Floor Attr",
                "default": "is_floor",
                "description": "Boolean written per face: true on the floor faces",
            },
        ),
        "sep2": ("Separator", {}),
        "enum_storey": (
            "Enum",
            {
                "name": "Storey From",
                "items": [
                    ("POINTER", "Object", "One storey for every space"),
                    ("ATTRIBUTE", "Attribute", "An index per face, 0 at the ground"),
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
                "description": (
                    "Index into the storeys ordered by height. A space takes "
                    "the LOWEST index among its faces, so a double-height room "
                    "belongs to the floor it starts on"
                ),
                "show_if": ("enum_storey", {"ATTRIBUTE"}),
            },
        ),
        "enum_renov_source": (
            "Enum",
            {
                "name": "Status From",
                "items": [
                    ("FIXED", "Fixed", "One status for every space"),
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
                "default": "N",
                "show_if": ("enum_renov_source", {"FIXED"}),
            },
        ),
        "precision": ("Int", {"name": "Rounding Decimals", "default": 3}),
    }

    # --- ENTRY POINT ---

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
        container_obj.name = f"SPACE_CONTAINER_{parent_obj.name}"

        storey_pointer = bim.entity(data_holder.story_pointer)
        key = bim.family_key(container_obj, data_holder.face_source)
        bim.cleanup_family(ifc_file, "IfcSpace", key)

        source = container_obj
        bm = bmesh.new()
        bm.from_mesh(source.data)
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        naked = sum(1 for edge in bm.edges if len(edge.link_faces) < 2)
        if naked:
            print(
                f"[RefOps] {naked} arestas soltas — os volumes vazam por elas e "
                "alguns ambientes podem se fundir."
            )

        bounding = cls._bounding_faces(bm, data_holder)
        if bounding is None:
            bm.free()
            return False

        cells = cls._decompose(bm, bounding)
        rooms = cls._measure(bm, cells, source.matrix_world, data_holder)
        if not rooms:
            print("[RefOps] Nenhum volume acima do mínimo.")
            bm.free()
            return False

        storey_layer = (
            bm.faces.layers.int.get(data_holder.storey_attr)
            if data_holder.storey_attr
            else None
        )
        renov_layer = (
            bm.faces.layers.int.get(data_holder.renov_attr)
            if data_holder.renov_attr
            else None
        )

        names = cls._room_names(bm, rooms, data_holder)
        body_context = bim.get_body_context(ifc_file)

        created = []
        for index, room in enumerate(rooms):
            element = cls._build_space(
                ifc_file,
                room=room,
                name=names[index],
                room_index=index,
                matrix_world=source.matrix_world,
                storey=cls._room_storey(
                    ifc_file, bm, room, storey_layer, storey_pointer, data_holder
                ),
                renov_code=cls._room_status(bm, room, renov_layer, data_holder),
                body_context=body_context,
                data_holder=data_holder,
                key=key,
            )
            if element:
                created.append(element)

        bm.free()
        print(f"[RefOps] {len(created)} ambientes criados.")
        return bool(created)

    # --- FACE SELECTION ---

    @classmethod
    def _bounding_faces(cls, bm, data_holder):
        """Which faces enclose space.

        In CONSTRUCTION mode a face only bounds a room if it is a wall, slab or
        roof. Unmarked faces are voids — openings, guides — and letting them
        bound would split one room into several."""
        if data_holder.face_source == "ALL_FACES":
            return {face.index for face in bm.faces}

        marked = set()
        for attribute in (
            data_holder.wall_attribute,
            data_holder.slab_attribute,
            data_holder.roof_attribute,
            data_holder.extra_attribute,
        ):
            if not attribute:
                continue
            layer = bm.faces.layers.bool.get(attribute)
            if layer is None:
                print(f"[RefOps] Atributo '{attribute}' não encontrado.")
                continue
            marked |= {face.index for face in bm.faces if face[layer]}

        if not marked:
            print("[RefOps] Nenhuma face marcada como elemento construtivo.")
            return None
        return marked

    # --- DECOMPOSITION ---

    @classmethod
    def _decompose(cls, bm, bounding):
        """Cells as (face index, side) sets, side +1 meaning the face's normal
        side.

        Every face contributes two nodes: in a non-manifold mesh a face
        separates two different pockets of space, and that duality is what makes
        one mesh yield many rooms."""
        parent = {}

        def find(node):
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(a, b):
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                parent[root_b] = root_a

        for face in bm.faces:
            if face.index not in bounding:
                continue
            for side in (1, -1):
                parent[(face.index, side)] = (face.index, side)

        for edge in bm.edges:
            faces = [f for f in edge.link_faces if f.index in bounding]
            if len(faces) < 2:
                continue
            ordered = cls._sort_around_edge(edge, faces)
            if not ordered:
                continue
            for position in range(len(ordered)):
                face_a, forward_a = ordered[position]
                face_b, forward_b = ordered[(position + 1) % len(ordered)]
                union((face_a.index, forward_a), (face_b.index, -forward_b))

        cells = {}
        for node in parent:
            cells.setdefault(find(node), []).append(node)
        return list(cells.values())

    @classmethod
    def _sort_around_edge(cls, edge, faces):
        """Faces ordered by angle around the edge, each with the side that looks
        toward increasing angle.

        Without that side, the union would pair the wrong faces and interior and
        exterior would merge into one cell."""
        import math

        origin = edge.verts[0].co
        axis = edge.verts[1].co - origin
        if axis.length < TOL:
            return None
        axis = axis.normalized()

        reference = mathutils.Vector((0.0, 0.0, 1.0))
        if abs(axis.dot(reference)) > 0.9:
            reference = mathutils.Vector((1.0, 0.0, 0.0))
        u = (reference - axis * reference.dot(axis)).normalized()
        v = axis.cross(u)

        entries = []
        for face in faces:
            direction = face.calc_center_median() - origin
            direction -= axis * direction.dot(axis)
            if direction.length < TOL:
                continue
            angle = math.atan2(direction.dot(v), direction.dot(u)) % (2 * math.pi)
            tangent = -math.sin(angle) * u + math.cos(angle) * v
            forward = 1 if face.normal.dot(tangent) > 0 else -1
            entries.append((angle, face, forward))

        if len(entries) < 2:
            return None
        entries.sort(key=lambda item: item[0])
        return [(face, forward) for _angle, face, forward in entries]

    # --- MEASUREMENT ---

    @classmethod
    def _measure(cls, bm, cells, matrix_world, data_holder):
        """Volume, floor area and outline per cell, discarding the outside.

        Volume comes from the divergence theorem over the cell's own outward
        normals: a face whose (+) side belongs to the cell has the cell on its
        normal side, so the outward normal there is the opposite. Only an
        enclosed cell integrates positive, which is what separates a room from
        the infinite region around the building."""
        rooms = []
        minimum = max(data_holder.min_volume, 0.0)

        for cell in cells:
            volume = 0.0
            floor_area = 0.0
            floor_faces = []
            faces = []
            lowest = None
            highest = None

            for face_index, side in cell:
                face = bm.faces[face_index]
                outward = face.normal * (-side)
                centre = face.calc_center_median()
                area = face.calc_area()
                volume += centre.dot(outward) * area / 3.0
                faces.append(face_index)
                for vert in face.verts:
                    z = (matrix_world @ vert.co).z
                    lowest = z if lowest is None else min(lowest, z)
                    highest = z if highest is None else max(highest, z)
                if outward.z < -0.7:
                    floor_area += area
                    floor_faces.append(face_index)

            if volume <= minimum or lowest is None:
                continue

            outline = cls._floor_outline(bm, floor_faces, matrix_world)
            if outline is None:
                print("[RefOps] Ambiente sem piso reconhecível — ignorado.")
                continue

            rooms.append(
                {
                    "faces": set(faces),
                    "floor_faces": set(floor_faces),
                    "volume": volume,
                    "area": floor_area,
                    "outline": outline,
                    "elevation": lowest,
                    "height": highest - lowest,
                }
            )

        rooms.sort(key=lambda room: -room["volume"])
        return rooms

    @classmethod
    def _floor_outline(cls, bm, floor_faces, matrix_world):
        """The floor's boundary as a 2D ring, wound counter-clockwise.

        Taken from the boundary edges of the floor faces rather than from one
        face, so a floor split by loops still yields a single outline."""
        if not floor_faces:
            return None
        members = set(floor_faces)
        boundary = []
        for face_index in floor_faces:
            for edge in bm.faces[face_index].edges:
                touching = sum(
                    1 for face in edge.link_faces if face.index in members
                )
                if touching == 1:
                    boundary.append(edge)
        if len(boundary) < 3:
            return None

        # Walk the boundary edges into a loop.
        adjacency = {}
        for edge in boundary:
            a, b = edge.verts
            adjacency.setdefault(a, []).append(b)
            adjacency.setdefault(b, []).append(a)

        start = next(iter(adjacency))
        loop = [start]
        previous, current = None, start
        while True:
            options = [v for v in adjacency.get(current, []) if v is not previous]
            if not options:
                break
            previous, current = current, options[0]
            if current is start:
                break
            loop.append(current)
            if len(loop) > len(adjacency):
                break

        if len(loop) < 3:
            return None

        points = []
        for vert in loop:
            world = matrix_world @ vert.co
            points.append((world.x, world.y))

        area2 = sum(
            points[i][0] * points[(i + 1) % len(points)][1]
            - points[(i + 1) % len(points)][0] * points[i][1]
            for i in range(len(points))
        )
        if abs(area2) < TOL:
            return None
        if area2 < 0:
            points.reverse()
        return points

    # --- NAMING ---

    @classmethod
    def _room_names(cls, bm, rooms, data_holder):
        prefix = (data_holder.name_prefix or "Room").strip() or "Room"
        layer = None
        if data_holder.room_id_attribute:
            layer = bm.faces.layers.int.get(data_holder.room_id_attribute)
            if layer is None:
                print(
                    f"[RefOps] Atributo '{data_holder.room_id_attribute}' "
                    "não encontrado."
                )

        names = []
        used = {}
        for position, room in enumerate(rooms):
            identifier = None
            if layer is not None:
                votes = {}
                for face_index in room["faces"]:
                    value = bm.faces[face_index][layer]
                    if value:
                        votes[value] = votes.get(value, 0) + 1
                if votes:
                    identifier = max(votes.items(), key=lambda item: item[1])[0]
            if identifier is None:
                identifier = position + 1
            name = f"{prefix}_{int(identifier):03d}"
            used[name] = used.get(name, 0) + 1
            if used[name] > 1:
                name = f"{name}.{used[name] - 1:02d}"
            names.append(name)
        return names

    # --- BUILD ---

    @classmethod
    def _build_space(cls, ifc_file, room, name, room_index, matrix_world, storey,
                     body_context, data_holder, key, renov_code="N"):
        """One IfcSpace: the floor outline extruded to the room height."""
        origin = mathutils.Vector(
            (
                sum(p[0] for p in room["outline"]) / len(room["outline"]),
                sum(p[1] for p in room["outline"]) / len(room["outline"]),
                room["elevation"],
            )
        )
        local = [(x - origin.x, y - origin.y) for x, y in room["outline"]]

        obj, element = bim.create_product(
            "IfcSpace", name, mathutils.Matrix.Translation(origin)
        )
        if not element:
            return None

        representation = bim.swept_solid_representation(
            ifc_file, body_context, local, room["height"]
        )
        bim.set_representation(ifc_file, obj, element, representation)

        if storey is not None:
            # A space is aggregated INTO the storey, not contained by it — that
            # is the relationship IFC defines for spatial nesting, and it is
            # what puts the room in the spatial tree instead of beside it.
            try:
                bim.run(
                    "aggregate.assign_object",
                    ifc_file,
                    products=[element],
                    relating_object=storey,
                )
            except Exception as e:
                print(f"[RefOps] aggregate.assign_object: {e}")
                bim.assign_container(ifc_file, element, storey)

        # "Not Set" leaves the status out of the file entirely.
        status = {"A": "EXISTING", "B": "DEMOLISH", "C": "NEW"}.get(renov_code)
        if status:
            bim.set_status(ifc_file, element, status)

        precision = data_holder.precision
        bim.set_pset(
            ifc_file,
            element,
            "Qto_SpaceBaseQuantities",
            {
                "GrossFloorArea": round(room["area"], precision),
                "GrossVolume": round(room["volume"], precision),
                "Height": round(room["height"], precision),
            },
        )
        bim.tag_family(ifc_file, element, key)

        placed = bim.object_for(element) or obj
        cls._write_attributes(placed, room_index)
        return element

    @classmethod
    def _room_storey(cls, ifc_file, bm, room, layer, pointer, data_holder):
        """The storey a space belongs to: the LOWEST index among its faces.

        A double-height room touches two levels, and it belongs to the one it
        starts on — measuring volume upward from the floor is what a space is
        for, and that is also what most tools report."""
        if data_holder.enum_storey != "ATTRIBUTE" or layer is None:
            return pointer
        values = [int(bm.faces[index][layer]) for index in room["faces"]]
        if not values:
            return pointer
        return bim.storey_by_index(min(values), ifc_file)

    @classmethod
    def _room_status(cls, bm, room, layer, data_holder):
        """Renovation status, by majority across the space's faces."""
        if data_holder.enum_renov_source != "ATTRIBUTE" or layer is None:
            return data_holder.enum_renov
        counts = {}
        for index in room["faces"]:
            value = int(bm.faces[index][layer])
            counts[value] = counts.get(value, 0) + 1
        if not counts:
            return data_holder.enum_renov
        winner = max(counts.items(), key=lambda item: item[1])[0]
        return bim.STATUS_TO_CODE.get(bim.status_from_index(winner), "N")

    @classmethod
    def _write_attributes(cls, obj, room_index):
        """Room number and floor flag, per face.

        Written after the body is built, because switch_representation replaces
        the mesh and would drop anything written earlier. The room number is an
        integer so a material can index off it; the floor flag is separate so a
        plate representation can pick the bottom faces alone."""
        mesh = getattr(obj, "data", None)
        if not mesh or not mesh.polygons:
            return

        index_attribute = mesh.attributes.get("room_index")
        if index_attribute is not None and (
            index_attribute.data_type != "INT" or index_attribute.domain != "FACE"
        ):
            mesh.attributes.remove(index_attribute)
            index_attribute = None
        if index_attribute is None:
            index_attribute = mesh.attributes.new(
                name="room_index", type="INT", domain="FACE"
            )
        index_attribute.data.foreach_set("value", [room_index] * len(mesh.polygons))

        floor_attribute = mesh.attributes.get("is_floor")
        if floor_attribute is not None and (
            floor_attribute.data_type != "BOOLEAN" or floor_attribute.domain != "FACE"
        ):
            mesh.attributes.remove(floor_attribute)
            floor_attribute = None
        if floor_attribute is None:
            floor_attribute = mesh.attributes.new(
                name="is_floor", type="BOOLEAN", domain="FACE"
            )
        # The space is a solid built by extrusion, so its own normals point out:
        # the floor is simply the face looking down.
        floor_attribute.data.foreach_set(
            "value", [polygon.normal.z < -0.7 for polygon in mesh.polygons]
        )
        mesh.update()
