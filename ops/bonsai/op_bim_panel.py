import hashlib

import bmesh
import bpy
import mathutils


bim = pyops_api.bim_helpers
prof = pyops_api.bim_profiles

TOL = bim.TOL


class BonsaiPanel:
    """One IFC element per selected face: an inset, offset, optionally extruded
    plate, with an optional profile mitred around its perimeter.

    IfcPlate is the class for this — IFC4 defines it as a planar part of
    constant thickness, which is exactly a panel (curtain-wall glazing is
    modelled as IfcPlate). The field is free text, so anything else works too.

    Faces are handled one at a time and never merged. Two panels sharing an
    edge stay two panels, each with its own inset, thickness and frame."""

    id = "BIM_PANEL"
    label = "Bonsai IFC Panel"
    icon = "MESH_PLANE"
    data_type = "MESH"
    execution_mode = "OBJECT"

    props_definition = {
        "lbl_sel": ("Label", {"text": "Face Selection", "icon": "RESTRICT_SELECT_OFF"}),
        "bool_attr": ("String", {"name": "Select Attr (face bool)", "search": "attribute"}),
        "sep1": ("Separator", {}),
        "lbl_shape": ("Label", {"text": "Panel Shape", "icon": "MOD_SOLIDIFY"}),
        "inset_attr": ("String", {"name": "Inset Attr", "search": "attribute"}),
        "extrude_attr": ("String", {"name": "Thickness Attr", "search": "attribute"}),
        "offset_attr": ("String", {"name": "Normal Offset Attr", "search": "attribute"}),
        "sep2": ("Separator", {}),
        "lbl_frame": ("Label", {"text": "Perimeter Profile", "icon": "MOD_THICKNESS"}),
        "enum_frame": (
            "Enum",
            {
                "name": "Build",
                "items": [
                    ("PANEL", "Panel Only", "Extrusion without a perimeter profile"),
                    ("BOTH", "Panel + Profile", "Extrusion with a profile around it"),
                    ("PROFILE", "Profile Only", "Perimeter frame with no panel"),
                ],
                "default": "PANEL",
            },
        ),
        "enum_profile_source": (
            "Enum",
            {
                "name": "Profile From",
                "items": [
                    ("OBJECT", "Object", "One mesh object"),
                    ("COLLECTION", "Collection", "Pick per face by weight attribute"),
                ],
                "default": "OBJECT",
                "show_if": ("enum_frame", {"BOTH", "PROFILE"}),
            },
        ),
        "profile_object": (
            "Object",
            {"name": "Profile Object", "show_if": ("enum_profile_source", {"OBJECT"})},
        ),
        "profile_collection": (
            "Collection",
            {"name": "Profile Collection", "show_if": ("enum_profile_source", {"COLLECTION"})},
        ),
        "profile_weight_attr": (
            "String",
            {
                "name": "Profile Weight Attr",
                "search": "attribute",
                "show_if": ("enum_profile_source", {"COLLECTION"}),
            },
        ),
        # The attribute names an IfcMaterialConstituentSet that must ALREADY be
        # in the file (same contract as Type Geometry). Minting one set per type
        # name left sets nobody could organise, so nothing is created here.
        **bim.material_props("face"),
        "sep3": ("Separator", {}),
        "lbl_bim": ("Label", {"text": "BIM Properties", "icon": "OUTLINER_OB_GROUP_INSTANCE"}),
        "ifc_class": (
            "String",
            {"name": "IFC Class", "default": "IfcPlate"},
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
        "storey_attr": (
            "String",
            {
                "name": "Storey Attr (face int)",
                "search": "attribute",
                "description": "Index into the storeys ordered by height, 0 at the ground",
                "show_if": ("enum_storey", {"ATTRIBUTE"}),
            },
        ),
        "story_pointer": ("Object", {"name": "Building Story", "show_if": ("enum_storey", {"POINTER"})}),
        "sep4": ("Separator", {}),
        "precision": ("Int", {"name": "Rounding Decimals", "default": 3}),
    }

    # --- ENTRY POINT ---

    @classmethod
    def execute(cls, container_obj, data_holder):
        if not bim.ensure():
            print("[PyOps] Bonsai indisponível:")
            print(bim.IMPORT_ERROR)
            return False

        parent_obj = bpy.data.objects.get(getattr(container_obj, "proc_parent_name", ""))
        if not parent_obj or parent_obj.type != "MESH":
            return False

        ifc_file = bim.get_file()
        if not ifc_file:
            print("[PyOps] Nenhum projeto IFC ativo.")
            return False

        ifc_class = (data_holder.ifc_class or "").strip()
        if not ifc_class.startswith("Ifc"):
            print(f"[PyOps] '{ifc_class}' não parece uma classe IFC.")
            return False
        type_class = f"{ifc_class}Type"

        container_obj.display_type = "WIRE"
        container_obj.hide_render = True
        container_obj.name = f"PANEL_CONTAINER_{parent_obj.name}"

        storey_pointer = bim.entity(data_holder.story_pointer)

        key = bim.family_key(container_obj, data_holder.bool_attr)
        bim.cleanup_family(ifc_file, "IfcElement", key)
        bim.cleanup_orphan_objects("PNL-", exclude=(container_obj,))

        body_context = bim.get_body_context(ifc_file)

        # Read the CONTAINER's mesh, not the parent's. The container is the
        # duplicate the engine made with the parent's viewport modifiers baked
        # in, so it carries the Geometry Nodes output for the current frame.
        # parent_obj.data is the original datablock and never changes as the
        # frame advances — reading it would make the result frame-independent.
        source = container_obj

        bm = bmesh.new()
        bm.from_mesh(source.data)
        bm.faces.ensure_lookup_table()

        select = bm.faces.layers.bool.get(data_holder.bool_attr)
        if not select:
            bm.free()
            print(f"[PyOps] Atributo booleano '{data_holder.bool_attr}' não encontrado.")
            return False

        def layer(name):
            return bm.faces.layers.float.get(name) if name else None

        inset_layer = layer(data_holder.inset_attr)
        extrude_layer = layer(data_holder.extrude_attr)
        offset_layer = layer(data_holder.offset_attr)
        weight_layer = layer(data_holder.profile_weight_attr)

        def int_layer(name):
            return bm.faces.layers.int.get(name) if name else None

        storey_layer = int_layer(data_holder.storey_attr)
        material_layer = int_layer(data_holder.material_attr)

        faces = [f for f in bm.faces if f[select]]
        if not faces:
            bm.free()
            return False

        weight_range = None
        if weight_layer:
            values = [f[weight_layer] for f in faces]
            weight_range = (min(values), max(values))

        precision = data_holder.precision
        wants_panel = data_holder.enum_frame in {"PANEL", "BOTH"}
        wants_profile = data_holder.enum_frame in {"PROFILE", "BOTH"}

        created = []  # (IFC element, type index)
        types = bim.TypeIndexer()
        for face in faces:
            inset = round(f_value(face, inset_layer), precision)
            thickness = round(f_value(face, extrude_layer), precision)
            offset = round(f_value(face, offset_layer), precision)

            profile_obj = None
            if wants_profile:
                profile_obj = cls._profile_object(
                    face, weight_layer, weight_range, data_holder
                )
                if not profile_obj:
                    print("[PyOps] Nenhum perfil resolvido para esta face.")
                    continue

            # One face is one panel here — nothing is ever merged — so the
            # vertex group table is read straight off it.
            mat_name = (
                bim.vgroup_name_from_faces(parent_obj, [face], material_layer) or ""
            )
            result = cls._build_panel(
                ifc_file,
                face=face,
                matrix_world=source.matrix_world,
                inset=inset,
                thickness=thickness,
                offset=offset,
                profile_obj=profile_obj,
                wants_panel=wants_panel,
                wants_profile=wants_profile,
                ifc_class=ifc_class,
                type_class=type_class,
                mat_name=mat_name,
                storey=(
                    bim.storey_by_index(int(face[storey_layer]), ifc_file)
                    if data_holder.enum_storey == "ATTRIBUTE" and storey_layer
                    else storey_pointer
                ),
                body_context=body_context,
                precision=precision,
                types=types,
                key=key,
            )
            if result:
                created.append(result)

        for element, type_index, body_material in created:
            obj = bim.object_for(element)
            if not obj:
                continue
            cls._apply_material_slots(obj, body_material)
            bim.set_type_index(obj, type_index)

        bm.free()
        return bool(created)

    # --- INPUT RESOLUTION ---

    @classmethod
    def _profile_object(cls, face, weight_layer, weight_range, data_holder):
        if data_holder.enum_profile_source == "OBJECT":
            return data_holder.profile_object
        objects = prof.ordered_objects(data_holder.profile_collection)
        if not objects:
            return None
        if not weight_layer or not weight_range:
            return objects[0]
        low, high = weight_range
        index = prof.weight_to_index(face[weight_layer], low, high, len(objects))
        return objects[index] if index is not None else None

    # --- BUILD ---

    @classmethod
    def _build_panel(cls, ifc_file, face, matrix_world, inset, thickness, offset,
                     profile_obj, wants_panel, wants_profile, ifc_class, type_class,
                     mat_name, storey, body_context, precision,
                     types, key):
        frame = cls._face_frame(face, matrix_world)
        if not frame:
            return None
        origin, x_axis, y_axis, normal = frame

        outline = cls._outline_2d(face, matrix_world, frame)
        if outline is None:
            return None
        if inset > TOL:
            outline = cls._inset_polygon(outline, inset)
            if outline is None:
                print("[PyOps] Inset maior que a face — painel descartado.")
                return None

        profile_points = None
        if wants_profile:
            profile_points = prof.polygon_from_object(profile_obj)
            if not profile_points:
                print(f"[PyOps] '{profile_obj.name}' não deu um contorno utilizável.")
                return None

        signature = cls._shape_signature(outline, precision)
        type_name = cls._type_name(
            signature, len(outline), thickness, mat_name, profile_obj
        )

        matrix = mathutils.Matrix(
            (
                (x_axis.x, y_axis.x, normal.x, origin.x),
                (x_axis.y, y_axis.y, normal.y, origin.y),
                (x_axis.z, y_axis.z, normal.z, origin.z),
                (0.0, 0.0, 0.0, 1.0),
            )
        )

        mesh = cls._assemble_mesh(
            type_name, outline, offset, thickness, profile_points,
            wants_panel, wants_profile,
        )
        if mesh is None:
            return None

        obj = bpy.data.objects.new(type_name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.matrix_world = matrix

        element = bim.assign_ifc_class(
            obj, ifc_class, add_representation=True, context=body_context
        )
        if not element:
            bpy.data.objects.remove(obj, do_unlink=True)
            return None

        body_material = None
        comp_type = bim.find_type_by_name(ifc_file, type_class, type_name)
        if comp_type is None:
            _obj, comp_type = bim.create_type(ifc_file, type_class, type_name)
        if comp_type:
            # Every run, not only on creation: repainting the index or renaming
            # the set must reach a type that already exists. Never creates a set.
            if mat_name:
                material_set = bim.find_constituent_set(ifc_file, mat_name)
                if material_set is None:
                    print(
                        f"[PyOps] Nenhum IfcMaterialConstituentSet '{mat_name}' "
                        f"no arquivo — '{type_name}' fica sem material."
                    )
                else:
                    bim.assign_material_set(ifc_file, comp_type, material_set)
                    body_material = bim.set_blender_material(material_set)
                    if body_material is None:
                        print(
                            f"[PyOps] O estilo de '{mat_name}' não tem material "
                            f"Blender — '{type_name}' fica sem cor."
                        )
            bim.assign_type(ifc_file, element, comp_type)

        bim.assign_container(ifc_file, element, storey)
        bim.tag_family(ifc_file, element, key)
        bim.file_object(obj)
        return element, types.index(comp_type), body_material

    @classmethod
    def _assemble_mesh(cls, name, outline, offset, thickness, profile_points,
                       wants_panel, wants_profile):
        verts, faces = [], []

        if wants_panel:
            if abs(thickness) > TOL:
                cls._append_prism(verts, faces, outline, offset, offset + thickness)
            else:
                # Zero thickness stays a single n-gon. Extruding by zero would
                # emit a second ring of vertices on top of the first.
                start = len(verts)
                verts.extend((x, y, offset) for x, y in outline)
                faces.append(list(range(start, start + len(outline))))

        if wants_profile and profile_points:
            # Mid-thickness: the profile's own origin rides the panel outline.
            height = offset + (thickness / 2.0 if abs(thickness) > TOL else 0.0)
            cls._append_frame(verts, faces, outline, profile_points, height)

        if not faces:
            return None

        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        return mesh

    @classmethod
    def _append_prism(cls, verts, faces, outline, z_low, z_high):
        count = len(outline)
        base = len(verts)
        verts.extend((x, y, z_low) for x, y in outline)
        verts.extend((x, y, z_high) for x, y in outline)
        faces.append(list(range(base + count - 1, base - 1, -1)))
        faces.append(list(range(base + count, base + 2 * count)))
        for i in range(count):
            j = (i + 1) % count
            faces.append([base + i, base + j, base + j + count, base + i + count])

    @classmethod
    def _append_frame(cls, verts, faces, outline, profile_points, height):
        """Closed sweep of the profile around the outline, mitred at the corners.

        A ring of profile points is placed at every outline vertex and the rings
        are bridged. Each ring is built on the corner's miter vector, so the
        profile keeps its width through a corner instead of pinching — the same
        vector the inset uses, for the same reason."""
        count = len(outline)
        ring_size = len(profile_points)
        base = len(verts)

        for i in range(count):
            corner = mathutils.Vector(outline[i])
            miter = cls._miter_vector(outline, i)
            for px, py in profile_points:
                point = corner + miter * px
                verts.append((point.x, point.y, height + py))

        for i in range(count):
            j = (i + 1) % count
            for k in range(ring_size):
                l = (k + 1) % ring_size
                faces.append(
                    [
                        base + i * ring_size + k,
                        base + i * ring_size + l,
                        base + j * ring_size + l,
                        base + j * ring_size + k,
                    ]
                )

    # --- GEOMETRY ---

    @classmethod
    def _face_frame(cls, face, matrix_world):
        """Orthonormal frame on the face plane, origin at its centroid. Local Z
        is the face normal, so offset and thickness are plain Z values."""
        rot = matrix_world.to_3x3()
        normal = (rot @ face.normal)
        if normal.length < TOL:
            return None
        normal.normalize()
        origin = matrix_world @ face.calc_center_median()

        seed = mathutils.Vector((0.0, 0.0, 1.0))
        if abs(normal.dot(seed)) > 0.999:
            seed = mathutils.Vector((1.0, 0.0, 0.0))
        x_axis = seed.cross(normal).normalized()
        y_axis = normal.cross(x_axis).normalized()
        return origin, x_axis, y_axis, normal

    @classmethod
    def _outline_2d(cls, face, matrix_world, frame):
        origin, x_axis, y_axis, _normal = frame
        points = []
        for vert in face.verts:
            delta = (matrix_world @ vert.co) - origin
            points.append((delta.dot(x_axis), delta.dot(y_axis)))
        if len(points) < 3:
            return None
        if cls._signed_area(points) < 0:
            points.reverse()  # counter-clockwise, so inward normals point inward
        return points

    @classmethod
    def _signed_area(cls, points):
        n = len(points)
        return 0.5 * sum(
            points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1]
            for i in range(n)
        )

    @classmethod
    def _miter_vector(cls, points, index):
        """Unit-inset direction at a vertex: the bisector, scaled so that moving
        along it by `d` shifts BOTH adjacent edges inward by exactly `d`."""
        n = len(points)
        previous = mathutils.Vector(points[index - 1])
        current = mathutils.Vector(points[index])
        following = mathutils.Vector(points[(index + 1) % n])

        edge_in = (current - previous)
        edge_out = (following - current)
        if edge_in.length < TOL or edge_out.length < TOL:
            return mathutils.Vector((0.0, 0.0))
        edge_in.normalize()
        edge_out.normalize()

        # Inward normal of a CCW edge (dx, dy) is (-dy, dx).
        normal_in = mathutils.Vector((-edge_in.y, edge_in.x))
        normal_out = mathutils.Vector((-edge_out.y, edge_out.x))
        denominator = 1.0 + normal_in.dot(normal_out)
        if abs(denominator) < 1e-4:
            return normal_in  # spike: fall back to one edge instead of exploding
        return (normal_in + normal_out) / denominator

    @classmethod
    def _inset_polygon(cls, points, distance):
        """Miter offset inward, rejected when the outline folds through itself.

        Area alone is not a sufficient test: inset a 2x2 square by 1.5 and every
        vertex crosses the centre, yielding a smaller square that is still
        counter-clockwise and still smaller than the original. What actually
        gives it away is an edge reversing direction, so that is what is checked
        — an edge whose inset copy points backwards means the offset overshot
        the medial axis."""
        count = len(points)
        moved = []
        for index in range(count):
            vertex = mathutils.Vector(points[index])
            vertex += cls._miter_vector(points, index) * distance
            moved.append((vertex.x, vertex.y))

        if cls._signed_area(moved) <= TOL:
            return None
        for index in range(count):
            following = (index + 1) % count
            before = mathutils.Vector(points[following]) - mathutils.Vector(points[index])
            after = mathutils.Vector(moved[following]) - mathutils.Vector(moved[index])
            if before.length < TOL or after.length < TOL:
                return None
            if before.dot(after) <= 0.0:
                return None
        return moved

    # --- TYPE IDENTITY ---

    @classmethod
    def _shape_signature(cls, points, precision):
        """Congruence signature: the cyclic sequence of (edge length, corner
        angle), canonicalised over every rotation and its mirror.

        Rotation-invariant and translation-invariant by construction, so two
        panels of the same shape share a type wherever they sit and however they
        are wound. Ordering is preserved — a plain sorted multiset of lengths
        and angles would collapse an S-shape and a Z-shape into one type."""
        n = len(points)
        pairs = []
        for i in range(n):
            previous = mathutils.Vector(points[i - 1])
            current = mathutils.Vector(points[i])
            following = mathutils.Vector(points[(i + 1) % n])
            length = round((following - current).length, precision)
            edge_in = previous - current
            edge_out = following - current
            if edge_in.length < TOL or edge_out.length < TOL:
                angle = 0.0
            else:
                angle = round(edge_in.angle(edge_out), precision)
            pairs.append((length, angle))

        candidates = [tuple(pairs[i:] + pairs[:i]) for i in range(n)]
        mirrored = list(reversed(pairs))
        candidates += [tuple(mirrored[i:] + mirrored[:i]) for i in range(n)]
        return min(candidates)

    @classmethod
    def _type_name(cls, signature, point_count, thickness, mat_name, profile_obj):
        digest = hashlib.blake2b(repr(signature).encode(), digest_size=2).hexdigest()
        name = f"PNL-{point_count}pt-{digest}-{int(round(thickness * 1000))}mm"
        if mat_name:
            name += f"_{mat_name}"
        if profile_obj:
            name += f"_{profile_obj.name}"
        return name

    # --- FINISHING ---

    @classmethod
    def _apply_material_slots(cls, obj, body_material):
        """One slot, the set's own Blender material. A new occurrence arrives
        with none, so a refresh would otherwise drop the colour. Nothing to put
        in the slot leaves the mesh as it is."""
        mesh = obj.data
        if not mesh or body_material is None:
            return
        mesh.materials.clear()
        mesh.materials.append(body_material)
        for slot in obj.material_slots:
            if slot.link != "DATA":
                slot.link = "DATA"
        for poly in mesh.polygons:
            poly.material_index = 0
        mesh.update()


def f_value(face, layer):
    return face[layer] if layer else 0.0
