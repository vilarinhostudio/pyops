"""Spatial graph from a single non-manifold architectural mesh.

    non-manifold mesh -> temporary volumetric cells -> one vertex per room
    -> edges where rooms connect -> vertices + edges, nothing else

The decomposition is the interesting part. A non-manifold mesh already encodes
the rooms: every interior face has geometry on both sides, and the volumes
between faces are the spaces. Finding them does not need a boolean engine or a
solver — it needs the faces sorted rotationally around each edge.

Around one edge the incident faces appear at increasing angles. Two faces that
are neighbours in that rotation bound the same pocket of space, so the side of
one and the facing side of the other belong to the same cell. Union those pairs
over every edge and the connected components ARE the rooms. That is what
Tissue's Polyhedra Decomposition does, and doing it here directly means the
operator has no addon dependency at all.

The infinite region outside the building falls out of the same process and is
identified by its volume: a proper cell encloses positive volume under the
divergence theorem, the outside does not.

Connections come from the IFC when it is available. A door is no longer a face
shared by two rooms — it is an IfcOpeningElement with a position and a direction
through the wall, so testing a point on either side of the void against the
cells finds the two rooms it joins. That works with the door off-centre, in a
corner, or anywhere the shared-face test used to miss.

Rooms can also be read from IfcSpace instead of being decomposed here, once the
space operator has produced them.

Nothing temporary survives. The container's mesh is replaced by the graph.
"""

import math

import bmesh
import bpy
import mathutils

import refops_api

bim = refops_api.bim_helpers

TOL = 1e-6


class SpaceSyntaxGraph:
    id = "SPACE_SYNTAX"
    label = "Space Syntax Graph"
    icon = "OUTLINER_OB_POINTCLOUD"
    data_type = "MESH"
    execution_mode = "OBJECT"

    # Connection kinds. Adding a row is the whole cost of a new access type —
    # the detection logic is the same for all of them.
    CONNECTION_KINDS = (
        ("door_attribute", "DOOR"),
        ("stair_attribute", "STAIR"),
        ("elevator_attribute", "ELEVATOR"),
    )

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
                "name": "Room Id Attr (int)",
                "search": "attribute",
                "description": "Optional face INT attribute naming each room; blank numbers them",
            },
        ),
        "sep1": ("Separator", {}),
        "lbl_links": ("Label", {"text": "Connections", "icon": "MOD_ARRAY"}),
        "enum_links": (
            "Enum",
            {
                "name": "From",
                "items": [
                    ("IFC", "IFC Openings", "Every void that joins two rooms"),
                    ("ATTRIBUTES", "Face Attributes", "Faces marked as doors and stairs"),
                ],
                "default": "IFC",
            },
        ),
        "door_attribute": ("String", {"name": "Doors", "search": "attribute",
             "show_if": ("enum_links", {"ATTRIBUTES"})}),
        "stair_attribute": ("String", {"name": "Stairs", "search": "attribute",
             "show_if": ("enum_links", {"ATTRIBUTES"})}),
        "elevator_attribute": ("String", {"name": "Elevators", "search": "attribute",
             "show_if": ("enum_links", {"ATTRIBUTES"})}),
        "host_class": (
            "String",
            {
                "name": "Host Class",
                "default": "IfcWall",
                "description": "Elements whose voids count as connections",
                "show_if": ("enum_links", {"IFC"}),
            },
        ),
        "connect_adjacent": (
            "Bool",
            {
                "name": "Connect All Shared Faces",
                "default": False,
                "description": "Link rooms sharing any face, not only the marked ones",
            },
        ),
        "sep2": ("Separator", {}),
        "vertex_group_prefix": ("String", {"name": "Group Prefix", "default": "Room"}),
        "collection_name": (
            "String",
            {"name": "Collection", "default": "Space Syntax"},
        ),
    }

    # --- ENTRY POINT ---

    @classmethod
    def execute(cls, container_obj, data_holder):
        source_mesh = container_obj.data
        if not source_mesh or not source_mesh.polygons:
            print("[RefOps.space] Objeto sem faces.")
            return False

        bm = bmesh.new()
        bm.from_mesh(source_mesh)
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()

        naked = sum(1 for edge in bm.edges if len(edge.link_faces) < 2)
        if naked:
            print(
                f"[RefOps.space] {naked} arestas soltas — os volumes vazam por elas "
                "e alguns espaços podem se fundir."
            )

        bounding = cls._bounding_faces(bm, data_holder)
        if bounding is None:
            bm.free()
            return False
        cells = cls._decompose(bm, bounding)
        if not cells:
            print("[RefOps.space] Nenhum volume fechado encontrado.")
            bm.free()
            return False

        rooms = cls._measure(bm, cells, container_obj.matrix_world, data_holder)
        if not rooms:
            print("[RefOps.space] Nenhum volume acima do mínimo.")
            bm.free()
            return False

        cls._collection_name = (
            data_holder.collection_name or "Space Syntax"
        ).strip() or "Space Syntax"
        if data_holder.enum_links == "IFC":
            connections = cls._ifc_connections(rooms, container_obj, data_holder)
        else:
            connections = cls._connections(bm, cells, rooms, data_holder)
        names = cls._room_names(bm, cells, rooms, data_holder)

        bm.free()
        cls._build_graph(container_obj, rooms, connections, names)
        return True

    # --- DECOMPOSITION ---

    @classmethod
    def _bounding_faces(cls, bm, data_holder):
        """Which faces enclose space.

        In CONSTRUCTION mode a face only bounds a room if it is a wall, slab,
        roof or whatever else was marked. Unmarked faces are voids — openings,
        guides, geometry that happens to be there — and letting them bound would
        split one room into several.

        Returns None when the mode is set but nothing is marked, since that
        would otherwise decompose an empty shell into nothing at all."""
        if data_holder.face_source == "ALL_FACES":
            return {face.index for face in bm.faces}

        marked = set()
        for attribute in (
            data_holder.wall_attribute,
            data_holder.slab_attribute,
            data_holder.roof_attribute,
            data_holder.extra_attribute,
        ):
            marked |= cls._marked_faces(bm, attribute)
        if not marked:
            print("[RefOps.space] Nenhuma face marcada como elemento construtivo.")
            return None
        return marked

    @classmethod
    def _decompose(cls, bm, bounding=None):
        """Cells as lists of (face index, side), side +1 meaning the region on
        the face's normal side.

        Every face contributes two nodes because a face in a non-manifold mesh
        separates two different pockets of space — that duality is what makes
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
            if bounding is not None and face.index not in bounding:
                continue
            for side in (1, -1):
                parent[(face.index, side)] = (face.index, side)

        for edge in bm.edges:
            faces = [
                face
                for face in edge.link_faces
                if bounding is None or face.index in bounding
            ]
            if len(faces) < 2:
                continue
            ordered = cls._sort_around_edge(edge, faces)
            if not ordered:
                continue
            count = len(ordered)
            for position in range(count):
                face_a, side_a_forward = ordered[position]
                face_b, side_b_forward = ordered[(position + 1) % count]
                # The pocket between consecutive faces touches the forward side
                # of the first and the backward side of the second.
                union((face_a.index, side_a_forward), (face_b.index, -side_b_forward))

        cells = {}
        for node in parent:
            cells.setdefault(find(node), []).append(node)
        return list(cells.values())

    @classmethod
    def _sort_around_edge(cls, edge, faces):
        """Faces sorted by their angle around the edge axis.

        Each entry is (face, forward_side): which side of that face looks toward
        increasing angle. Without this the union would pair the wrong sides and
        interior and exterior would merge into one cell."""
        origin = edge.verts[0].co
        axis = (edge.verts[1].co - origin)
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
            # Tangent pointing toward increasing angle at this position.
            tangent = -math.sin(angle) * u + math.cos(angle) * v
            forward = 1 if face.normal.dot(tangent) > 0 else -1
            entries.append((angle, face, forward))

        if len(entries) < 2:
            return None
        entries.sort(key=lambda item: item[0])
        return [(face, forward) for _angle, face, forward in entries]

    # --- MEASUREMENT ---

    @classmethod
    def _measure(cls, bm, cells, matrix, data_holder):
        """Volume, floor area and centre per cell, discarding the outside.

        Volume comes from the divergence theorem over the cell's own outward
        normals. A face whose (+) side belongs to the cell has the cell sitting
        on its normal side, so the cell's outward normal there is the opposite —
        which is exactly what separates a room from the infinite region around
        the building, since only the enclosed one integrates positive."""
        rooms = []
        minimum = max(data_holder.min_volume, 0.0)

        for cell in cells:
            volume = 0.0
            floor_area = 0.0
            weighted_centre = mathutils.Vector((0.0, 0.0, 0.0))
            total_area = 0.0

            for face_index, side in cell:
                face = bm.faces[face_index]
                outward = face.normal * (-side)
                centre = face.calc_center_median()
                area = face.calc_area()
                volume += centre.dot(outward) * area / 3.0
                weighted_centre += centre * area
                total_area += area
                if outward.z < -0.7:
                    floor_area += area

            if volume <= minimum or total_area < TOL:
                continue
            centre = weighted_centre / total_area
            triangles = []
            for face_index, side in cell:
                face = bm.faces[face_index]
                points = [matrix @ vert.co for vert in face.verts]
                for position in range(1, len(points) - 1):
                    triangles.append((points[0], points[position], points[position + 1]))

            rooms.append(
                {
                    "triangles": triangles,
                    "cell": cell,
                    "faces": {face_index for face_index, _side in cell},
                    "sides": {(face_index, side) for face_index, side in cell},
                    "volume": volume,
                    "area": floor_area if floor_area > TOL else total_area / 6.0,
                    "centre": matrix @ centre,
                }
            )

        rooms.sort(key=lambda room: (-room["volume"]))
        return rooms

    # --- CONNECTIONS ---

    @classmethod
    def _connections(cls, bm, cells, rooms, data_holder):
        """Edges of the graph.

        A door is a face whose two sides land in two different rooms, so the
        connection is already in the decomposition — no proximity test, no
        tolerance. Stairs and lifts are the same idea over a group of faces:
        every room touching one connected patch of marked faces is linked to the
        others, which handles a stairwell spanning several floors."""
        room_of_side = {}
        for position, room in enumerate(rooms):
            for key in room["sides"]:
                room_of_side[key] = position

        connections = {}

        def link(a, b, kind):
            if a is None or b is None or a == b:
                return
            key = (min(a, b), max(a, b))
            connections.setdefault(key, set()).add(kind)

        for attribute_name, kind in cls.CONNECTION_KINDS:
            marked = cls._marked_faces(bm, getattr(data_holder, attribute_name, ""))
            if not marked:
                continue
            if kind == "DOOR":
                for face_index in marked:
                    link(
                        room_of_side.get((face_index, 1)),
                        room_of_side.get((face_index, -1)),
                        kind,
                    )
                continue
            # Vertical access: an island of marked faces joins every room it
            # touches, so a stair shaft crossing three storeys links all three.
            for island in cls._islands(bm, marked):
                touched = set()
                for face_index in island:
                    for side in (1, -1):
                        room = room_of_side.get((face_index, side))
                        if room is not None:
                            touched.add(room)
                ordered = sorted(touched)
                for position in range(len(ordered) - 1):
                    link(ordered[position], ordered[position + 1], kind)

        if data_holder.connect_adjacent:
            for face in bm.faces:
                link(
                    room_of_side.get((face.index, 1)),
                    room_of_side.get((face.index, -1)),
                    "ADJACENT",
                )

        return connections

    @classmethod
    def _ifc_connections(cls, rooms, container_obj, data_holder):
        """Rooms joined by the voids cut into their hosts.

        An IfcOpeningElement is a solid that goes THROUGH a wall, so a point
        just outside each of its two faces falls in the rooms on either side.
        Which rooms those are is answered by the decomposition that already ran,
        not by geometry matching — so a door placed off-centre, or in a corner,
        or filled by a window, is found the same way.

        Falls back to the attribute method when Bonsai or the IFC is absent, so
        the operator still works on a mesh alone."""
        if not bim.ensure():
            print("[RefOps.space] Bonsai indisponível — usando atributos.")
            return {}
        ifc_file = bim.get_file()
        if ifc_file is None:
            print("[RefOps.space] Nenhum projeto IFC — usando atributos.")
            return {}

        host_class = (data_holder.host_class or "IfcWall").strip()
        try:
            hosts = ifc_file.by_type(host_class)
        except Exception:
            print(f"[RefOps.space] '{host_class}' não existe no schema.")
            return {}

        connections = {}
        found = 0
        for host in hosts:
            for relation in getattr(host, "HasOpenings", None) or []:
                opening = relation.RelatedOpeningElement
                obj = bim.object_for(opening)
                if obj is None or obj.data is None:
                    continue
                probes = cls._void_probes(obj)
                if probes is None:
                    continue
                found += 1
                first = cls._room_at(rooms, probes[0])
                second = cls._room_at(rooms, probes[1])
                if first is None or second is None or first == second:
                    continue
                pair = (min(first, second), max(first, second))
                connections.setdefault(pair, set()).add("DOOR")

        print(
            f"[RefOps.space] {found} aberturas lidas, "
            f"{len(connections)} conexões encontradas."
        )
        return connections

    @classmethod
    def _void_probes(cls, obj):
        """Two points, one just beyond each face of the void.

        The void's own longest bounding-box axis is the one that crosses the
        wall, so stepping along it from the centre lands inside the rooms on
        either side. Stepping a little further than the half-length clears the
        wall itself."""
        corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
        centre = sum(corners, mathutils.Vector()) / len(corners)

        rotation = obj.matrix_world.to_3x3()
        best_axis, best_length = None, 0.0
        for index in range(3):
            axis = rotation.col[index]
            projections = [(corner - centre).dot(axis.normalized()) for corner in corners]
            length = max(projections) - min(projections)
            if length > best_length:
                best_axis, best_length = axis.normalized(), length
        if best_axis is None or best_length < TOL:
            return None

        step = best_length * 0.75
        return centre + best_axis * step, centre - best_axis * step

    @classmethod
    def _room_at(cls, rooms, point):
        """Which room contains a point.

        Tested against each cell's own faces by ray parity: a ray from the point
        crosses an odd number of the cell's faces exactly when it starts inside.
        Bounding boxes would answer wrongly for an L-shaped room."""
        for index, room in enumerate(rooms):
            if cls._inside(room, point):
                return index
        return None

    @classmethod
    def _inside(cls, room, point):
        direction = mathutils.Vector((0.5773, 0.5774, 0.5773))  # nothing axis-aligned
        crossings = 0
        for triangle in room.get("triangles", ()):
            a, b, c = triangle
            hit = mathutils.geometry.intersect_ray_tri(a, b, c, direction, point, True)
            if hit is not None:
                crossings += 1
        return crossings % 2 == 1

    @classmethod
    def _marked_faces(cls, bm, attribute_name):
        if not attribute_name:
            return set()
        layer = bm.faces.layers.bool.get(attribute_name)
        if layer is None:
            print(f"[RefOps.space] Atributo booleano '{attribute_name}' não encontrado.")
            return set()
        return {face.index for face in bm.faces if face[layer]}

    @classmethod
    def _islands(cls, bm, indices):
        remaining = set(indices)
        islands = []
        while remaining:
            start = remaining.pop()
            island = {start}
            stack = [start]
            while stack:
                face = bm.faces[stack.pop()]
                for edge in face.edges:
                    for linked in edge.link_faces:
                        if linked.index in remaining:
                            remaining.discard(linked.index)
                            island.add(linked.index)
                            stack.append(linked.index)
            islands.append(island)
        return islands

    # --- NAMING ---

    @classmethod
    def _room_names(cls, bm, cells, rooms, data_holder):
        """Vertex group names.

        String attributes don't round-trip in this workflow, so an INT face
        attribute is the way to carry an author-chosen identifier; without one
        the rooms are numbered by descending volume, which at least stays stable
        between refreshes as long as the geometry does."""
        prefix = (data_holder.vertex_group_prefix or "Room").strip() or "Room"
        layer = None
        if data_holder.room_id_attribute:
            layer = bm.faces.layers.int.get(data_holder.room_id_attribute)
            if layer is None:
                print(
                    f"[RefOps.space] Atributo int "
                    f"'{data_holder.room_id_attribute}' não encontrado."
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

    # --- OUTPUT ---

    @classmethod
    def _move_to_collection(cls, obj, name):
        """Put the graph in its own collection at scene level.

        It is an abstraction, not building geometry — keeping it beside the
        walls makes both harder to read, and a named collection can be hidden
        with one click."""
        collection = bpy.data.collections.get(name)
        if collection is None:
            collection = bpy.data.collections.new(name)
            bpy.context.scene.collection.children.link(collection)
        elif collection.name not in bpy.context.scene.collection.children:
            try:
                bpy.context.scene.collection.children.link(collection)
            except RuntimeError:
                pass  # already linked somewhere in the scene
        for current in list(obj.users_collection):
            if current is not collection:
                current.objects.unlink(obj)
        if obj.name not in collection.objects:
            collection.objects.link(obj)

    @classmethod
    def _build_graph(cls, obj, rooms, connections, names):
        """The container's mesh becomes the graph — vertices and edges only.

        Replacing it rather than adding an object is what keeps the temporary
        volumes from ever reaching the scene: they only existed as index sets
        inside this function."""
        matrix_inverse = obj.matrix_world.inverted()
        coordinates = [matrix_inverse @ room["centre"] for room in rooms]
        edges = sorted(connections.keys())

        graph = bpy.data.meshes.new(f"{obj.name}_graph")
        graph.from_pydata([tuple(point) for point in coordinates], edges, [])
        graph.update()

        for name, values in (
            ("area", [room["area"] for room in rooms]),
            ("volume", [room["volume"] for room in rooms]),
        ):
            layer = graph.attributes.new(name=name, type="FLOAT", domain="POINT")
            layer.data.foreach_set("value", values)

        # Connection kind as a number on the edge, so the graph carries how two
        # rooms are linked and not merely that they are.
        kinds = {"DOOR": 1, "STAIR": 2, "ELEVATOR": 3, "ADJACENT": 4}
        if edges:
            layer = graph.attributes.new(
                name="connection", type="FLOAT", domain="EDGE"
            )
            layer.data.foreach_set(
                "value",
                [
                    float(min(kinds.get(kind, 0) for kind in connections[edge]))
                    for edge in edges
                ],
            )

        cls._move_to_collection(obj, cls._collection_name)

        old = obj.data
        obj.data = graph
        if old and old.users == 0:
            bpy.data.meshes.remove(old)

        obj.vertex_groups.clear()
        for index, name in enumerate(names):
            group = obj.vertex_groups.new(name=name)
            group.add([index], 1.0, "REPLACE")

        obj["refops_operator"] = "space_syntax"
        obj["rooms"] = len(rooms)
        obj["connections"] = len(edges)
        obj["total_volume"] = round(sum(room["volume"] for room in rooms), 3)
        obj["total_area"] = round(sum(room["area"] for room in rooms), 3)
