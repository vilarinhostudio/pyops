"""Shared machinery for edge-driven IFC components (columns, beams, profiles).

Operator files subclass EdgeComponent and set four class attributes; everything
else — profile resolution, placement, offsets, cardinal alignment, end
components, type naming — lives here so the three operators stay identical
where they should be.

    import refops_api
    class BonsaiColumn(refops_api.bim_profiles.EdgeComponent):
        id = "BIM_COLUMN"
        IFC_CLASS = "IfcColumn"
        ...

Two facts shape the design:

* His numpad alignment scheme IS IFC's CardinalPoint. 1..9 read bottom-left to
  top-right in exactly the same order, so alignment maps straight onto
  IfcMaterialProfileSetUsage.CardinalPoint instead of needing a translation.

* Adding a start or end mesh takes the element out of the parametric world.
  A profile extrusion is one swept solid Bonsai can edit; a swept solid with
  arbitrary meshes welded to its ends is just geometry. The build forks on that,
  and the fork is the reason PARAMETRIC and MESH exist as separate paths below.
"""

import math

import bmesh
import bpy
import mathutils

from . import bim_helpers as bim

TOL = bim.TOL

# CardinalPoint layout, matching a numpad:
#   7 8 9   top    left / centre / right
#   4 5 6   middle
#   1 2 3   bottom
CARDINAL_DEFAULT = 5


def cardinal_offset(cardinal, half_x, half_y):
    """Where the section sits relative to the reference line. Cardinal 1 means
    the profile's bottom-left corner rides the line, so the section shifts
    +half_x, +half_y away from it."""
    if not 1 <= cardinal <= 9:
        cardinal = CARDINAL_DEFAULT
    column = (cardinal - 1) % 3  # 0 left, 1 centre, 2 right
    row = (cardinal - 1) // 3  # 0 bottom, 1 middle, 2 top
    return (1 - column) * half_x, (1 - row) * half_y


def weight_to_index(value, low, high, count):
    """Maps an arbitrary float range onto a list of N objects by proximity.

    Works with any N: two profiles and random values in 0..1 split at the
    midpoint, because the weight is scaled to N-1 and rounded. A degenerate
    range (every edge carrying the same value) has no ranking to express, so
    everything lands on the first object."""
    if count <= 0:
        return None
    if count == 1 or high - low < TOL:
        return 0
    weight = (value - low) / (high - low)
    return max(0, min(count - 1, int(round(weight * (count - 1)))))


def ordered_objects(collection):
    """Collection order is the mapping order — sorted by name, since Blender's
    collection ordering in the outliner is alphabetical."""
    if not collection:
        return []
    return sorted(collection.objects, key=lambda o: o.name)


def polygon_from_object(obj):
    """Ordered 2D outline of a mesh used as a profile, in its own local XY.

    Prefers a face when the mesh has one; otherwise walks the edge loop, which
    is what you get from a curve converted to mesh. Wound counter-clockwise so
    the extruded solid's normals point outward."""
    if not obj or obj.type != "MESH" or not obj.data:
        return None
    mesh = obj.data

    if mesh.polygons:
        face = max(mesh.polygons, key=lambda p: p.area)
        points = [(mesh.vertices[i].co.x, mesh.vertices[i].co.y) for i in face.vertices]
    else:
        adjacency = {}
        for edge in mesh.edges:
            a, b = edge.vertices
            adjacency.setdefault(a, []).append(b)
            adjacency.setdefault(b, []).append(a)
        if not adjacency:
            return None
        start = next((v for v, n in adjacency.items() if len(n) == 2), None)
        if start is None:
            return None
        order = [start]
        previous, current = None, start
        while True:
            nexts = [v for v in adjacency[current] if v != previous]
            if not nexts:
                break
            previous, current = current, nexts[0]
            if current == start:
                break
            order.append(current)
        points = [(mesh.vertices[i].co.x, mesh.vertices[i].co.y) for i in order]

    if len(points) < 3:
        return None
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


def bounds_2d(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0


def edge_basis(direction, reference):
    """Orthonormal basis with local Z along the edge and local Y as close to the
    reference direction as it can be. IfcExtrudedAreaSolid runs along +Z, so the
    edge has to be the Z axis; the reference decides the roll."""
    z_axis = direction.normalized()
    if reference is None or reference.length < TOL:
        reference = mathutils.Vector((0.0, 0.0, 1.0))
    if abs(z_axis.dot(reference.normalized())) > 0.999:
        # Reference parallel to the edge gives no roll information — a vertical
        # edge with a Z-up reference is the common case.
        reference = mathutils.Vector((0.0, 1.0, 0.0))
        if abs(z_axis.dot(reference)) > 0.999:
            reference = mathutils.Vector((1.0, 0.0, 0.0))
    x_axis = reference.cross(z_axis).normalized()
    y_axis = z_axis.cross(x_axis).normalized()
    return x_axis, y_axis, z_axis


def prism_mesh(name, points_2d, length, offset_2d=(0.0, 0.0)):
    """Closed prism from a 2D outline extruded along +Z — the Blender-side twin
    of the IFC swept solid, for when end components rule out the IFC path."""
    dx, dy = offset_2d
    ring = [(x + dx, y + dy) for x, y in points_2d]
    bottom = [(x, y, 0.0) for x, y in ring]
    top = [(x, y, length) for x, y in ring]
    count = len(ring)

    verts = bottom + top
    faces = [list(range(count - 1, -1, -1)), list(range(count, 2 * count))]
    for i in range(count):
        j = (i + 1) % count
        faces.append([i, j, j + count, i + count])

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def append_object_mesh(target_mesh, source_obj, placement):
    """Weld a copy of source_obj's mesh into target_mesh at `placement`, which is
    expressed in the target's local space. The source's own origin is the anchor,
    so an end cap sits where its origin sits."""
    if not source_obj or source_obj.type != "MESH" or not source_obj.data:
        return
    bm = bmesh.new()
    bm.from_mesh(target_mesh)

    src = bmesh.new()
    src.from_mesh(source_obj.data)
    src.transform(placement)

    offset = len(bm.verts)
    bm.verts.ensure_lookup_table()
    src.verts.ensure_lookup_table()
    new_verts = [bm.verts.new(v.co) for v in src.verts]
    bm.verts.ensure_lookup_table()
    for face in src.faces:
        try:
            bm.faces.new([new_verts[v.index] for v in face.verts])
        except ValueError:
            pass  # duplicate face, harmless
    src.free()

    bm.to_mesh(target_mesh)
    bm.free()
    target_mesh.update()
    return offset


class _MergedEdge:
    """A run of collinear edges seen as one.

    Only what the rest of the operator touches is provided: the two extreme
    vertices, an index, and attribute lookup delegated to the first edge — the
    attributes of a chain are the attributes of where it starts."""

    def __init__(self, edges, matrix_world):
        self._first = edges[0]
        self.index = edges[0].index
        points = []
        for edge in edges:
            points.extend(edge.verts)
        far_a, far_b = None, None
        best = -1.0
        for a in points:
            for b in points:
                distance = ((matrix_world @ a.co) - (matrix_world @ b.co)).length
                if distance > best:
                    best, far_a, far_b = distance, a, b
        self.verts = [far_a, far_b]
        self.link_faces = list(self._first.link_faces)

    def __getitem__(self, layer):
        return self._first[layer]


class EdgeComponent:
    """Base for every edge-driven component. Subclasses set id/label/icon plus
    the four IFC attributes below; nothing else needs overriding."""

    IFC_CLASS = "IfcMember"
    TYPE_CLASS = "IfcMemberType"
    PREFIX = "PRF"
    # Columns take their height from the Z difference alone and stand at the
    # lower vertex, so a slanted edge still produces a plumb column.
    VERTICAL_ONLY = False

    data_type = "MESH"
    execution_mode = "OBJECT"

    props_definition = {
        "lbl_sel": ("Label", {"text": "Edge Selection", "icon": "RESTRICT_SELECT_OFF"}),
        "bool_attr": ("String", {"name": "Select Attr (edge bool)", "search": "attribute"}),
        "sep1": ("Separator", {}),
        "lbl_profile": ("Label", {"text": "Profile", "icon": "MOD_THICKNESS"}),
        "enum_profile": (
            "Enum",
            {
                "name": "Profile",
                "items": [
                    ("NONE", "None (ends only)", "No extrusion — only the end components"),
                    ("RECT", "Rectangle", "Width and depth from float attributes"),
                    ("CIRCLE", "Circle", "Radius from a float attribute"),
                    ("TYPE", "Existing Type", "Reuse an IFC type via object pointer"),
                    ("OBJECT", "Blender Object", "Outline of a mesh object"),
                    ("COLLECTION", "Collection", "Pick per edge by weight attribute"),
                ],
                "default": "RECT",
            },
        ),
        "width_attr": (
            "String",
            {"name": "Width Attr", "search": "attribute", "show_if": ("enum_profile", {"RECT"})},
        ),
        "depth_attr": (
            "String",
            {"name": "Depth Attr", "search": "attribute", "show_if": ("enum_profile", {"RECT"})},
        ),
        "radius_attr": (
            "String",
            {"name": "Radius Attr", "search": "attribute", "show_if": ("enum_profile", {"CIRCLE"})},
        ),
        "type_pointer": (
            "Object",
            {"name": "Type Ref", "show_if": ("enum_profile", {"TYPE"})},
        ),
        "profile_object": (
            "Object",
            {"name": "Profile Object", "show_if": ("enum_profile", {"OBJECT"})},
        ),
        "profile_collection": (
            "Collection",
            {"name": "Profile Collection", "show_if": ("enum_profile", {"COLLECTION"})},
        ),
        "profile_weight_attr": (
            "String",
            {
                "name": "Profile Weight Attr",
                "search": "attribute",
                "show_if": ("enum_profile", {"COLLECTION"}),
            },
        ),
        "sep2": ("Separator", {}),
        "lbl_orient": ("Label", {"text": "Orientation & Alignment", "icon": "ORIENTATION_GIMBAL"}),
        "enum_orientation": (
            "Enum",
            {
                "name": "Orientation",
                "items": [
                    ("AUTO", "Automatic", "World Z up, falling back for vertical edges"),
                    ("FACE", "Face Normal", "Average normal of the faces sharing the edge"),
                    ("VECTOR", "Vector Attr", "Float-vector attribute on the edge"),
                ],
                "default": "AUTO",
            },
        ),
        "orient_attr": (
            "String",
            {
                "name": "Vector Attr",
                "search": "attribute",
                "show_if": ("enum_orientation", {"VECTOR"}),
            },
        ),
        "merge_collinear": (
            "Bool",
            {
                "name": "Merge Collinear Edges",
                "default": False,
                "description": (
                    "Chain edges that continue in the same direction into one "
                    "element — a column split by loops counts once"
                ),
            },
        ),
        "vertex_split_attr": (
            "String",
            {
                "name": "Split Attr (vert bool)",
                "search": "attribute",
                "description": "Vertices a merged run may not pass through",
                "show_if": ("merge_collinear", {True}),
            },
        ),
        "material_index_attr": (
            "String",
            {
                "name": "Material Index Attr (edge int)",
                "search": "attribute",
                "description": (
                    "Index into the reference object's material slots; blank "
                    "uses the first slot"
                ),
            },
        ),
        "cardinal_attr": (
            "String",
            {"name": "Cardinal Attr (edge int 1-9)", "search": "attribute"},
        ),
        "sep3": ("Separator", {}),
        "enum_offsets": (
            "Enum",
            {
                "name": "Offsets",
                "items": [
                    ("NONE", "Full Edge", "Extrude the whole edge"),
                    ("ATTRS", "From Attributes", "Trim the interval by attributes"),
                ],
                "default": "NONE",
            },
        ),
        "offset_start_attr": (
            "String",
            {"name": "Start Offset Attr", "search": "attribute", "show_if": ("enum_offsets", {"ATTRS"})},
        ),
        "offset_end_attr": (
            "String",
            {"name": "End Offset Attr", "search": "attribute", "show_if": ("enum_offsets", {"ATTRS"})},
        ),
        "offset_invert_attr": (
            "String",
            {"name": "Invert Attr (bool)", "search": "attribute", "show_if": ("enum_offsets", {"ATTRS"})},
        ),
        "sep4": ("Separator", {}),
        "lbl_ends": ("Label", {"text": "End Components", "icon": "SNAP_MIDPOINT"}),
        "enum_start": (
            "Enum",
            {
                "name": "Start",
                "items": [
                    ("NONE", "None", "Nothing at the start"),
                    ("OBJECT", "Object", "One mesh object"),
                    ("COLLECTION", "Collection", "Pick per edge by weight attribute"),
                ],
                "default": "NONE",
            },
        ),
        "start_object": ("Object", {"name": "Start Object", "show_if": ("enum_start", {"OBJECT"})}),
        "start_collection": (
            "Collection",
            {"name": "Start Collection", "show_if": ("enum_start", {"COLLECTION"})},
        ),
        "start_weight_attr": (
            "String",
            {"name": "Start Weight Attr", "search": "attribute", "show_if": ("enum_start", {"COLLECTION"})},
        ),
        "enum_end": (
            "Enum",
            {
                "name": "End",
                "items": [
                    ("NONE", "None", "Nothing at the end"),
                    ("OBJECT", "Object", "One mesh object"),
                    ("COLLECTION", "Collection", "Pick per edge by weight attribute"),
                ],
                "default": "NONE",
            },
        ),
        "end_object": ("Object", {"name": "End Object", "show_if": ("enum_end", {"OBJECT"})}),
        "end_collection": (
            "Collection",
            {"name": "End Collection", "show_if": ("enum_end", {"COLLECTION"})},
        ),
        "end_weight_attr": (
            "String",
            {"name": "End Weight Attr", "search": "attribute", "show_if": ("enum_end", {"COLLECTION"})},
        ),
        "ends_invert_attr": (
            "String",
            {"name": "Ends Invert Attr (bool)", "search": "attribute"},
        ),
        "sep5": ("Separator", {}),
        "lbl_bim": ("Label", {"text": "BIM Properties", "icon": "OUTLINER_OB_GROUP_INSTANCE"}),
        "enum_storey": (
            "Enum",
            {
                "name": "Storey From",
                "items": [
                    ("POINTER", "Object", "One storey for the whole run"),
                    ("ATTRIBUTE", "Attribute", "An index per element, 0 at the ground"),
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
                "name": "Storey Attr (edge int)",
                "search": "attribute",
                "description": (
                    "Index into the storeys ordered by height, 0 at the ground"
                ),
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
                "name": "Status Attr (edge int)",
                "search": "attribute",
                "show_if": ("enum_renov_source", {"ATTRIBUTE"}),
            },
        ),
        "body_material": (
            "Material",
            {
                "name": "Fallback Material",
                "description": "Used when the index attribute is absent or out of range",
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
                "default": "C",
                "show_if": ("enum_renov_source", {"FIXED"}),
            },
        ),
        "sep6": ("Separator", {}),
        "cut_material": ("String", {"name": "Cut Material", "search": "material"}),
        "cutter_object": ("Object", {"name": "Cutter Object"}),
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
        container_obj.name = f"{cls.PREFIX}_CONTAINER_{parent_obj.name}"

        # Resolve the pointer inputs BEFORE any cleanup. data_holder lives on
        # the container, so anything that could remove an object has to run
        # after the values are already in local variables.
        storey_pointer = bim.entity(data_holder.story_pointer)

        key = bim.family_key(container_obj, data_holder.bool_attr)
        bim.cleanup_family(ifc_file, cls.IFC_CLASS, key)
        # Type objects are named "<PREFIX>-...", with a dash — "<PREFIX>_"
        # matched nothing except the container itself.
        bim.cleanup_orphan_objects(f"{cls.PREFIX}-", exclude=(container_obj,))

        body_context = bim.get_body_context(ifc_file)

        # Read the CONTAINER's mesh, not the parent's. The container is the
        # duplicate the engine made with the parent's viewport modifiers baked
        # in, so it carries the Geometry Nodes output for the current frame.
        # parent_obj.data is the original datablock and never changes as the
        # frame advances — reading it would make the result frame-independent.
        source = container_obj

        bm = bmesh.new()
        bm.from_mesh(source.data)
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()

        layers = cls._layers(bm, data_holder)
        if layers is None:
            bm.free()
            return False

        edges = [e for e in bm.edges if e[layers["select"]]]
        if not edges:
            bm.free()
            return False

        if data_holder.merge_collinear:
            edges = cls._merge_collinear(
                edges, source.matrix_world, layers["vertex_split"]
            )

        # Which Blender material each element ended up with, so the finishing
        # pass can put it in slot 1 without recomputing.
        cls._materials = {}
        ranges = cls._weight_ranges(edges, layers)
        created = []  # (IFC element, type index)
        types = bim.TypeIndexer()
        for edge in edges:
            result = cls._build_edge(
                ifc_file,
                edge=edge,
                layers=layers,
                ranges=ranges,
                source=source,
                data_holder=data_holder,
                storey=storey_pointer,
                body_context=body_context,
                types=types,
                key=key,
            )
            if result:
                created.append(result)  # (element, type index)

        cut_material = (
            bpy.data.materials.get(data_holder.cut_material)
            if data_holder.cut_material
            else None
        )
        for element, type_index in created:
            obj = bim.object_for(element)
            if not obj:
                continue
            cls._apply_material_slots(
                obj,
                cut_material,
                cls._materials.get(element.id(), data_holder.body_material),
            )
            bim.set_type_index(obj, type_index)
            cls._add_boolean_cut(obj, data_holder.cutter_object)

        bm.free()
        return bool(created)

    @classmethod
    def _merge_collinear(cls, edges, matrix_world, split_layer=None):
        """Chain edges that continue straight into a single virtual edge.

        A column crossed by floor loops arrives as one edge per storey, and each
        becomes its own element — three columns where the schedule should read
        one.

        The chain grows from its OPEN ENDS: the vertices that belong to exactly
        one edge of the chain so far. Growing from `chain[0]` and `chain[-1]`
        instead — which is what this did — only works when edges happen to be
        appended in geometric order, and they are not, so runs stopped halfway
        and left collinear edges unjoined.

        A vertex marked in split_layer is a barrier: the chain stops there even
        though the geometry continues, which is how a column is cut per storey
        while still being merged within each one.

        Returns objects that quack like a bmesh edge for the rest of the
        pipeline: .verts, .index and attribute lookup, taken from the first edge
        of the chain."""
        remaining = set(edges)
        merged = []

        while remaining:
            seed = remaining.pop()
            direction = cls._edge_direction(seed, matrix_world)
            if direction is None:
                merged.append(seed)
                continue

            chain = [seed]
            # How many edges of the chain touch each vertex. A count of one
            # means the chain can still grow there.
            degree = {}
            for vert in seed.verts:
                degree[vert] = degree.get(vert, 0) + 1

            grew = True
            while grew:
                grew = False
                open_ends = [
                    vert for vert, count in degree.items()
                    if count == 1 and not (split_layer and vert[split_layer])
                ]
                for vert in open_ends:
                    for candidate in list(remaining):
                        if vert not in candidate.verts:
                            continue
                        other = cls._edge_direction(candidate, matrix_world)
                        if other is None or abs(other.dot(direction)) < 0.999:
                            continue
                        remaining.discard(candidate)
                        chain.append(candidate)
                        for end in candidate.verts:
                            degree[end] = degree.get(end, 0) + 1
                        grew = True
                        break
                    if grew:
                        break

            merged.append(
                _MergedEdge(chain, matrix_world) if len(chain) > 1 else seed
            )

        return merged

    @classmethod
    def _edge_direction(cls, edge, matrix_world):
        a, b = (matrix_world @ vert.co for vert in edge.verts)
        delta = b - a
        return delta.normalized() if delta.length > TOL else None

    # --- ATTRIBUTE BINDING ---

    @classmethod
    def _layers(cls, bm, data_holder):
        select = bm.edges.layers.bool.get(data_holder.bool_attr)
        if not select:
            print(f"[RefOps] Atributo booleano de edge '{data_holder.bool_attr}' não encontrado.")
            return None

        def edge_float(name):
            return bm.edges.layers.float.get(name) if name else None

        def edge_int(name):
            return bm.edges.layers.int.get(name) if name else None

        def edge_bool(name):
            return bm.edges.layers.bool.get(name) if name else None

        return {
            "select": select,
            "cardinal": edge_int(data_holder.cardinal_attr),
            "material_index": edge_int(data_holder.material_index_attr),
            "storey": edge_int(data_holder.storey_attr),
            "renov": edge_int(data_holder.renov_attr),
            "vertex_split": (
                bm.verts.layers.bool.get(data_holder.vertex_split_attr)
                if data_holder.vertex_split_attr
                else None
            ),
            "width": edge_float(data_holder.width_attr),
            "depth": edge_float(data_holder.depth_attr),
            "radius": edge_float(data_holder.radius_attr),
            "profile_weight": edge_float(data_holder.profile_weight_attr),
            "orient": (
                bm.edges.layers.float_vector.get(data_holder.orient_attr)
                if data_holder.orient_attr
                else None
            ),
            "offset_start": edge_float(data_holder.offset_start_attr),
            "offset_end": edge_float(data_holder.offset_end_attr),
            "offset_invert": edge_bool(data_holder.offset_invert_attr),
            "start_weight": edge_float(data_holder.start_weight_attr),
            "end_weight": edge_float(data_holder.end_weight_attr),
            "ends_invert": edge_bool(data_holder.ends_invert_attr),
        }

    @classmethod
    def _edge_int(cls, edge, layer, default=0):
        """An integer attribute on the edge."""
        if layer is None:
            return default
        try:
            return int(edge[layer])
        except Exception:
            return default

    @classmethod
    def _edge_material(cls, edge, layers, source, data_holder):
        """The Blender material for one edge, by slot index.

        An integer per edge beats a single material pointer: one operation can
        then carry a whole run of columns in different materials, and the index
        is paintable like every other attribute. Out of range falls back rather
        than failing — an unpainted attribute reads as zero, and zero is a valid
        slot."""
        materials = getattr(source.data, "materials", None)
        if not materials:
            return data_holder.body_material

        index = bim.read_int(edge, {"EDGE": layers["material_index"]}, "EDGE", 0)
        if 0 <= index < len(materials) and materials[index] is not None:
            return materials[index]
        if index >= len(materials):
            print(
                f"[RefOps] Índice de material {index} fora dos "
                f"{len(materials)} slots; usando o material de reserva."
            )
        return data_holder.body_material or (materials[0] if materials else None)

    @classmethod
    def _weight_ranges(cls, edges, layers):
        """min/max per weight attribute, computed once across the whole
        selection — the mapping only means anything relative to the set."""
        ranges = {}
        for name in ("profile_weight", "start_weight", "end_weight"):
            layer = layers[name]
            if not layer:
                continue
            values = [e[layer] for e in edges]
            ranges[name] = (min(values), max(values)) if values else (0.0, 0.0)
        return ranges

    @classmethod
    def _pick(cls, edge, layers, ranges, weight_name, collection):
        objects = ordered_objects(collection)
        if not objects:
            return None
        layer = layers[weight_name]
        if not layer or weight_name not in ranges:
            return objects[0]
        low, high = ranges[weight_name]
        index = weight_to_index(edge[layer], low, high, len(objects))
        return objects[index] if index is not None else None

    # --- PLACEMENT ---

    @classmethod
    def _segment(cls, edge, layers, matrix_world, data_holder):
        """Returns (origin, basis, length) in world space, or None.

        For a column the edge only supplies a footprint and a height: the axis is
        forced to +Z, the length is the Z difference, and the origin sits at the
        lower vertex. Everything else follows the edge as drawn."""
        p0 = matrix_world @ edge.verts[0].co
        p1 = matrix_world @ edge.verts[1].co

        invert = bool(edge[layers["offset_invert"]]) if layers["offset_invert"] else False
        if invert:
            p0, p1 = p1, p0

        if cls.VERTICAL_ONLY:
            low, high = (p0, p1) if p0.z <= p1.z else (p1, p0)
            origin = low.copy()
            direction = mathutils.Vector((0.0, 0.0, 1.0))
            length = high.z - low.z
        else:
            delta = p1 - p0
            length = delta.length
            if length < TOL:
                return None
            origin = p0.copy()
            direction = delta / length

        if data_holder.enum_offsets == "ATTRS":
            start = edge[layers["offset_start"]] if layers["offset_start"] else 0.0
            end = edge[layers["offset_end"]] if layers["offset_end"] else 0.0
            origin = origin + direction * start
            length = length - start - end

        if length < TOL:
            return None

        reference = cls._reference_vector(edge, layers, matrix_world, data_holder)
        return origin, edge_basis(direction, reference), length

    @classmethod
    def _reference_vector(cls, edge, layers, matrix_world, data_holder):
        mode = data_holder.enum_orientation
        if mode == "VECTOR" and layers["orient"]:
            raw = edge[layers["orient"]]
            return matrix_world.to_3x3() @ mathutils.Vector(tuple(raw))
        if mode == "FACE":
            # An edge has no normal of its own — the honest stand-in is the
            # average of the faces that share it. A wire edge has none, so this
            # falls through to the automatic reference.
            faces = list(edge.link_faces)
            if faces:
                total = mathutils.Vector((0.0, 0.0, 0.0))
                for f in faces:
                    total += matrix_world.to_3x3() @ f.normal
                if total.length > TOL:
                    return total
        return mathutils.Vector((0.0, 0.0, 1.0))

    # --- PROFILE ---

    @classmethod
    def _profile(cls, ifc_file, edge, layers, ranges, data_holder):
        """Returns (ifc_profile, points_2d_or_None, half_x, half_y, name).

        points_2d is present only for outline-based profiles; the rectangle and
        circle stay parametric IFC sections, which is what keeps them editable.
        The half extents are what the cardinal offset needs."""
        mode = data_holder.enum_profile
        precision = data_holder.precision

        if mode == "RECT":
            width = round(edge[layers["width"]], precision) if layers["width"] else 0.0
            depth = round(edge[layers["depth"]], precision) if layers["depth"] else 0.0
            if width <= 0 or depth <= 0:
                return None
            name = f"Rect-{int(width * 1000)}x{int(depth * 1000)}"
            profile = ifc_file.createIfcRectangleProfileDef(
                ProfileType="AREA", ProfileName=name, XDim=width, YDim=depth
            )
            corners = [
                (-width / 2, -depth / 2),
                (width / 2, -depth / 2),
                (width / 2, depth / 2),
                (-width / 2, depth / 2),
            ]
            return profile, corners, width / 2, depth / 2, name

        if mode == "CIRCLE":
            radius = round(edge[layers["radius"]], precision) if layers["radius"] else 0.0
            if radius <= 0:
                return None
            name = f"Circ-{int(radius * 2000)}"
            profile = ifc_file.createIfcCircleProfileDef(
                ProfileType="AREA", ProfileName=name, Radius=radius
            )
            segments = 32
            circle = [
                (
                    radius * math.cos(2 * math.pi * i / segments),
                    radius * math.sin(2 * math.pi * i / segments),
                )
                for i in range(segments)
            ]
            return profile, circle, radius, radius, name

        if mode == "OBJECT":
            source = data_holder.profile_object
        elif mode == "COLLECTION":
            source = cls._pick(
                edge, layers, ranges, "profile_weight", data_holder.profile_collection
            )
        else:
            return None

        points = polygon_from_object(source)
        if not points:
            return None
        half_x, half_y = bounds_2d(points)
        name = source.name
        polyline = ifc_file.createIfcPolyline(
            Points=[
                ifc_file.createIfcCartesianPoint((float(x), float(y)))
                for x, y in points + [points[0]]
            ]
        )
        profile = ifc_file.createIfcArbitraryClosedProfileDef(
            ProfileType="AREA", ProfileName=name, OuterCurve=polyline
        )
        return profile, points, half_x, half_y, name

    # --- BUILD ---

    @classmethod
    def _build_edge(cls, ifc_file, edge, layers, ranges, source, data_holder,
                    storey, body_context, types, key):
        segment = cls._segment(edge, layers, source.matrix_world, data_holder)
        if not segment:
            return None
        origin, (x_axis, y_axis, z_axis), length = segment

        cardinal = (
            int(edge[layers["cardinal"]]) if layers["cardinal"] else CARDINAL_DEFAULT
        )

        ends_invert = bool(edge[layers["ends_invert"]]) if layers["ends_invert"] else False
        start_obj = cls._end_object(edge, layers, ranges, data_holder, "start")
        end_obj = cls._end_object(edge, layers, ranges, data_holder, "end")
        if ends_invert:
            start_obj, end_obj = end_obj, start_obj

        matrix = mathutils.Matrix(
            (
                (x_axis.x, y_axis.x, z_axis.x, origin.x),
                (x_axis.y, y_axis.y, z_axis.y, origin.y),
                (x_axis.z, y_axis.z, z_axis.z, origin.z),
                (0.0, 0.0, 0.0, 1.0),
            )
        )

        if data_holder.enum_profile == "TYPE":
            comp_type = bim.entity(data_holder.type_pointer)
            if not comp_type or not comp_type.is_a(cls.TYPE_CLASS):
                print(f"[RefOps] Type Ref não é um {cls.TYPE_CLASS}.")
                return None
            # The section has to come from the type's profile set — there is no
            # attribute to read it from in this mode. No profile set means no
            # body to sweep.
            profile = bim.profile_of_type(comp_type)
            if profile is None:
                print(
                    f"[RefOps] '{comp_type.Name}' não tem IfcMaterialProfileSet — "
                    "sem perfil para extrudar."
                )
                return None
            half_x, half_y = bim.profile_half_extents(profile)
            profile_data = (
                profile,
                None,  # no outline: this path can't build a Blender prism
                half_x,
                half_y,
                profile.ProfileName or comp_type.Name or "Type",
            )
        elif data_holder.enum_profile == "NONE":
            # Ends only — a piston, a fitting, anything where the edge just
            # positions two pieces and nothing spans between them.
            if not (start_obj or end_obj):
                print("[RefOps] Perfil 'None' exige um componente de início ou fim.")
                return None
            profile_data = None
            comp_type = None
        else:
            profile_data = cls._profile(ifc_file, edge, layers, ranges, data_holder)
            if not profile_data:
                return None
            comp_type = None

        material = cls._edge_material(edge, layers, source, data_holder)
        material_name = material.name if material else "Default_Material"
        profile_name = profile_data[4] if profile_data else "Ends"
        type_name = cls._type_name(
            start_obj, profile_name, length, material_name, end_obj
        )

        if comp_type is None:
            comp_type = bim.find_type_by_name(ifc_file, cls.TYPE_CLASS, type_name)
            if comp_type is None:
                comp_type = cls._create_type(
                    ifc_file,
                    type_name,
                    profile_data[0] if profile_data else None,
                    material_name,
                    data_holder,
                    body_context,
                    material,
                )
            if comp_type is None:
                return None

        if start_obj or end_obj:
            obj, element = cls._build_mesh_element(
                ifc_file, matrix, profile_data, length, cardinal,
                start_obj, end_obj, body_context, type_name,
            )
        else:
            obj, element = cls._build_parametric_element(
                ifc_file, matrix, profile_data, length, cardinal, body_context, type_name
            )
        if not element:
            return None

        bim.assign_type(ifc_file, element, comp_type)
        cls._assign_cardinal(ifc_file, element, cardinal)
        if data_holder.enum_storey == "ATTRIBUTE":
            storey = bim.storey_by_index(
                cls._edge_int(edge, layers["storey"]), ifc_file
            )
        bim.assign_container(ifc_file, element, storey)
        # "Not Set" leaves the status out of the file entirely.
        if data_holder.enum_renov_source == "ATTRIBUTE":
            status = bim.status_from_index(
                cls._edge_int(edge, layers["renov"])
            )
        else:
            status = {"A": "EXISTING", "B": "DEMOLISH", "C": "NEW"}.get(
                data_holder.enum_renov
            )
        if status:
            bim.set_status(ifc_file, element, status)
        bim.tag_family(ifc_file, element, key)
        cls._materials[element.id()] = material
        return element, types.index(comp_type)

    @classmethod
    def _build_parametric_element(cls, ifc_file, matrix, profile_data, length,
                                  cardinal, body_context, name):
        if not profile_data:
            return None, None
        profile, _points, half_x, half_y, _n = profile_data
        obj, element = bim.create_product(cls.IFC_CLASS, name, matrix)
        if not element:
            return None, None
        representation = bim.extruded_representation(
            ifc_file, body_context, profile, length,
            offset_2d=cardinal_offset(cardinal, half_x, half_y),
        )
        bim.set_representation(ifc_file, obj, element, representation)
        return obj, element

    @classmethod
    def _build_mesh_element(cls, ifc_file, matrix, profile_data, length, cardinal,
                            start_obj, end_obj, body_context, name):
        """End components weld arbitrary geometry onto the swept body, so there
        is no single profile left to sweep — the whole thing is built in Blender
        and handed to IFC as a plain body."""
        points = profile_data[1] if profile_data else None
        if profile_data and not points:
            print(
                "[RefOps] 'Existing Type' não fornece contorno para varrer — "
                "use Rectangle/Circle/Object/Collection, ou perfil 'None'."
            )
            return None, None

        if points:
            _profile, _pts, half_x, half_y = profile_data[:4]
            mesh = prism_mesh(name, points, length, cardinal_offset(cardinal, half_x, half_y))
        else:
            mesh = bpy.data.meshes.new(name)  # ends only

        if start_obj:
            append_object_mesh(mesh, start_obj, mathutils.Matrix.Identity(4))
        if end_obj:
            append_object_mesh(
                mesh, end_obj, mathutils.Matrix.Translation((0.0, 0.0, length))
            )

        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.matrix_world = matrix
        element = bim.assign_ifc_class(
            obj, cls.IFC_CLASS, add_representation=True, context=body_context
        )
        if not element:
            bpy.data.objects.remove(obj, do_unlink=True)
            return None, None
        bim.file_object(obj)
        return obj, element

    @classmethod
    def _end_object(cls, edge, layers, ranges, data_holder, which):
        mode = getattr(data_holder, f"enum_{which}")
        if mode == "OBJECT":
            return getattr(data_holder, f"{which}_object")
        if mode == "COLLECTION":
            return cls._pick(
                edge, layers, ranges, f"{which}_weight",
                getattr(data_holder, f"{which}_collection"),
            )
        return None

    @classmethod
    def _create_type(cls, ifc_file, type_name, profile, material_name, data_holder,
                     body_context, material=None):
        type_obj, comp_type = bim.create_type(ifc_file, cls.TYPE_CLASS, type_name)
        if not comp_type:
            return None
        ifc_mat = bim.get_or_create_material(ifc_file, material_name)
        if material is not None:
            bim.sync_material_style(ifc_file, ifc_mat, material, body_context)
        if profile is not None:
            bim.assign_profile_set(ifc_file, comp_type, type_name, profile, ifc_mat)
        return comp_type

    @classmethod
    def _assign_cardinal(cls, ifc_file, element, cardinal):
        """The usage carries the alignment in IFC terms; the geometry already
        has it baked as a profile offset, so the two agree."""
        try:
            bim.assign_usage(ifc_file, element, "IfcMaterialProfileSetUsage")
            usage = bim.ifcopenshell.util.element.get_material(element)
            if usage and usage.is_a("IfcMaterialProfileSetUsage"):
                usage.CardinalPoint = int(cardinal)
        except Exception as e:
            print(f"[RefOps] CardinalPoint: {e}")

    @classmethod
    def _type_name(cls, start_obj, profile_name, length, material_name, end_obj):
        """<start>_<PREFIX>-<profile>-<length>mm_<material>_<end>

        Length appears ONLY when there are end components. A plain extrusion of
        the same section is the same type whatever its length — putting the
        length in the name spawned one IfcType per edge. With end components the
        length is genuinely part of the assembly, so it earns its place."""
        core = f"{cls.PREFIX}-{profile_name}_{material_name}"
        if start_obj or end_obj:
            core = f"{cls.PREFIX}-{profile_name}-{int(round(length * 1000))}mm_{material_name}"
        if start_obj:
            core = f"{start_obj.name}_{core}"
        if end_obj:
            core = f"{core}_{end_obj.name}"
        return core

    # --- FINISHING ---

    @classmethod
    def _apply_material_slots(cls, obj, cut_material, body_material):
        """Same slot contract as the face-based operators:
            slot 0 -> cut material
            slot 1 -> body material

        Edges carry no material of their own, so slot 1 comes from an input
        rather than being inherited."""
        mesh = obj.data
        if not mesh:
            return
        mesh.materials.clear()
        mesh.materials.append(cut_material)
        mesh.materials.append(body_material)
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
