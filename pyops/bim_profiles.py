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

* The type is an INPUT, never invented. Section and material both come out of
  the type's IfcMaterialProfileSet, so neither can disagree with the model —
  which is why the width/depth/radius attributes and the type-minting code they
  fed are gone. Note the set class: IfcMaterialProfileSetUsage is an occurrence
  entity, so the type is checked for IfcMaterialProfileSet instead.

Every element is one swept solid, so it stays parametric in Bonsai. End
components used to weld arbitrary meshes onto the ends and forked the build
into a second, non-parametric path; both are gone, and with them the 'None
(ends only)' profile mode that existed to serve them.
"""

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
        "bool_attr": (
            "String",
            {"name": "Select Attr (edge bool)", "search": "attribute"},
        ),
        "sep1": ("Separator", {}),
        "lbl_profile": ("Label", {"text": "Profile", "icon": "MOD_THICKNESS"}),
        "enum_profile": (
            "Enum",
            {
                "name": "Profile",
                "items": [
                    ("TYPE", "Object Type (Pointer)", "Use the type object pointer"),
                    (
                        "COLLECTION",
                        "Collection (Weighted)",
                        "Pick a type per edge by weight",
                    ),
                ],
                "default": "TYPE",
            },
        ),
        "type_pointer": (
            "Object",
            {"name": "Type Ref", "show_if": ("enum_profile", {"TYPE"})},
        ),
        "type_collection": (
            "Collection",
            {"name": "Type Collection", "show_if": ("enum_profile", {"COLLECTION"})},
        ),
        "type_weight_attr": (
            "String",
            {
                "name": "Type Weight Attr",
                "search": "attribute",
                "show_if": ("enum_profile", {"COLLECTION"}),
            },
        ),
        "sep2": ("Separator", {}),
        "lbl_orient": (
            "Label",
            {"text": "Orientation & Alignment", "icon": "ORIENTATION_GIMBAL"},
        ),
        "enum_orientation": (
            "Enum",
            {
                "name": "Orientation",
                "items": [
                    (
                        "AUTO",
                        "Automatic",
                        "World Z up, falling back for vertical edges",
                    ),
                    (
                        "FACE",
                        "Face Normal",
                        "Average normal of the faces sharing the edge",
                    ),
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
            {
                "name": "Start Offset Attr",
                "search": "attribute",
                "show_if": ("enum_offsets", {"ATTRS"}),
            },
        ),
        "offset_end_attr": (
            "String",
            {
                "name": "End Offset Attr",
                "search": "attribute",
                "show_if": ("enum_offsets", {"ATTRS"}),
            },
        ),
        "offset_invert_attr": (
            "String",
            {
                "name": "Invert Attr (bool)",
                "search": "attribute",
                "show_if": ("enum_offsets", {"ATTRS"}),
            },
        ),
        "sep5": ("Separator", {}),
        "lbl_bim": (
            "Label",
            {"text": "BIM Properties", "icon": "OUTLINER_OB_GROUP_INSTANCE"},
        ),
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
        "sep6": ("Separator", {}),
        "precision": ("Int", {"name": "Rounding Decimals", "default": 3}),
    }

    # --- ENTRY POINT ---

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
        container_obj.hide_render = True
        container_obj.name = f"{cls.PREFIX}_CONTAINER_{parent_obj.name}"

        # Only the generic profile component exposes the field; a column is an
        # IfcColumn by definition, so it never offers the choice.
        ifc_class = (getattr(data_holder, "ifc_class", "") or cls.IFC_CLASS).strip()
        if not ifc_class.startswith("Ifc"):
            print(f"[RefOps] '{ifc_class}' não parece uma classe IFC.")
            return False
        type_class = f"{ifc_class}Type"

        # Resolve the pointer inputs BEFORE any cleanup. data_holder lives on
        # the container, so anything that could remove an object has to run
        # after the values are already in local variables.
        storey_pointer = bim.entity(data_holder.story_pointer)

        key = bim.family_key(container_obj, data_holder.bool_attr)
        # Swept as IfcElement rather than the exact class: by_type includes
        # subclasses, so a refresh still finds the previous run's output after
        # the class field has been changed.
        bim.cleanup_family(ifc_file, "IfcElement", key)
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

        weight_range = cls._weight_range(edges, layers)
        created = []  # (IFC element, type index)
        types = bim.TypeIndexer()
        for edge in edges:
            result = cls._build_edge(
                ifc_file,
                edge=edge,
                layers=layers,
                weight_range=weight_range,
                source=source,
                data_holder=data_holder,
                storey=storey_pointer,
                body_context=body_context,
                types=types,
                key=key,
                ifc_class=ifc_class,
                type_class=type_class,
            )
            if result:
                created.append(result)

        for element, type_index in created:
            obj = bim.object_for(element)
            if not obj:
                continue
            bim.set_type_index(obj, type_index)

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
                    vert
                    for vert, count in degree.items()
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

            merged.append(_MergedEdge(chain, matrix_world) if len(chain) > 1 else seed)

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
            print(
                f"[RefOps] Atributo booleano de edge '{data_holder.bool_attr}' não encontrado."
            )
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
            "storey": edge_int(data_holder.storey_attr),
            "vertex_split": (
                bm.verts.layers.bool.get(data_holder.vertex_split_attr)
                if data_holder.vertex_split_attr
                else None
            ),
            "type_weight": edge_float(data_holder.type_weight_attr),
            "orient": (
                bm.edges.layers.float_vector.get(data_holder.orient_attr)
                if data_holder.orient_attr
                else None
            ),
            "offset_start": edge_float(data_holder.offset_start_attr),
            "offset_end": edge_float(data_holder.offset_end_attr),
            "offset_invert": edge_bool(data_holder.offset_invert_attr),
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
    def _weight_range(cls, edges, layers):
        """min/max of the type weight across the whole selection — the mapping
        only means anything relative to the set."""
        layer = layers["type_weight"]
        if not layer:
            return None
        values = [e[layer] for e in edges]
        return (min(values), max(values)) if values else (0.0, 0.0)

    @classmethod
    def _pick(cls, edge, layers, weight_range, collection):
        objects = ordered_objects(collection)
        if not objects:
            return None
        layer = layers["type_weight"]
        if not layer or weight_range is None:
            return objects[0]
        low, high = weight_range
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

        invert = (
            bool(edge[layers["offset_invert"]]) if layers["offset_invert"] else False
        )
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
    def _resolve_type(cls, edge, layers, weight_range, data_holder, type_class):
        """The type for one edge — pointed at, or picked from a collection.

        Nothing here creates a type any more. The section used to be built from
        width/depth/radius attributes and a type minted around it; now the type
        is the input and the section is read out of its IfcMaterialProfileSet,
        so an attribute can no longer disagree with the model."""
        if data_holder.enum_profile == "COLLECTION":
            picked = cls._pick(
                edge, layers, weight_range, data_holder.type_collection
            )
            if picked is None:
                print("[RefOps] Nenhum tipo na collection para esta aresta.")
                return None
            source_obj = picked
        else:
            source_obj = data_holder.type_pointer
            if not source_obj:
                print("[RefOps] Nenhum Type Ref definido.")
                return None

        comp_type = bim.entity(source_obj)
        if comp_type is None or not comp_type.is_a(type_class):
            print(f"[RefOps] '{source_obj.name}' não é um {type_class}.")
            return None
        return comp_type

    @classmethod
    def _profile_of(cls, comp_type):
        """(ifc_profile, half_x, half_y) from the type's profile set. The half
        extents are what the cardinal offset needs."""
        profile = bim.profile_of_type(comp_type)
        if profile is None:
            print(
                f"[RefOps] '{comp_type.Name}' não tem IfcMaterialProfileSet — "
                "sem perfil para extrudar."
            )
            return None
        half_x, half_y = bim.profile_half_extents(profile)
        return profile, half_x, half_y

    # --- BUILD ---

    @classmethod
    def _build_edge(
        cls,
        ifc_file,
        edge,
        layers,
        weight_range,
        source,
        data_holder,
        storey,
        body_context,
        types,
        key,
        ifc_class,
        type_class,
    ):
        segment = cls._segment(edge, layers, source.matrix_world, data_holder)
        if not segment:
            return None
        origin, (x_axis, y_axis, z_axis), length = segment

        cardinal = (
            int(edge[layers["cardinal"]]) if layers["cardinal"] else CARDINAL_DEFAULT
        )

        matrix = mathutils.Matrix(
            (
                (x_axis.x, y_axis.x, z_axis.x, origin.x),
                (x_axis.y, y_axis.y, z_axis.y, origin.y),
                (x_axis.z, y_axis.z, z_axis.z, origin.z),
                (0.0, 0.0, 0.0, 1.0),
            )
        )

        comp_type = cls._resolve_type(edge, layers, weight_range, data_holder, type_class)
        if comp_type is None:
            return None

        # No profile set on the type stops the element: the section is READ from
        # it, so there is nothing left to sweep.
        if bim.resolve_type_material(comp_type, "IfcMaterialProfileSet") is None:
            return None

        section = cls._profile_of(comp_type)
        if not section:
            return None
        profile, half_x, half_y = section

        obj, element = cls._build_element(
            ifc_file,
            matrix,
            profile,
            cardinal_offset(cardinal, half_x, half_y),
            length,
            body_context,
            comp_type.Name or profile.ProfileName or "Type",
            ifc_class,
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
        bim.tag_family(ifc_file, element, key)
        return element, types.index(comp_type)

    @classmethod
    def _build_element(
        cls, ifc_file, matrix, profile, offset_2d, length, body_context, name,
        ifc_class,
    ):
        obj, element = bim.create_product(ifc_class, name, matrix)
        if not element:
            return None, None
        representation = bim.extruded_representation(
            ifc_file, body_context, profile, length, offset_2d=offset_2d
        )
        bim.set_representation(ifc_file, obj, element, representation)
        return obj, element

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
