"""Ladybug Tools → Blender bridge.

Lives in the addon rather than in an operator file so both `ladybug_graphs`
and the planned `ladybug_analysis` can share it:

    import refops_api
    lb = refops_api.ladybug_helpers

Two things shape this module.

Ladybug is resolved on FIRST USE, not at import time — same reason as
bim_helpers: the library may be installed into Blender's Python after the addon
has already registered, and a failed module-level import is cached by Python for
the rest of the session.

Ladybug is a family of packages, not one. ladybug-core is the base;
ladybug-comfort adds the comfort charts; ladybug-radiance adds sky matrices and
radiation studies and is frequently absent. Each is probed separately so a
missing optional package disables its own graphs instead of the operator.
"""

import math
import traceback

import bpy
import mathutils

core = None
geometry = None
comfort = None
radiance = None

AVAILABLE = False
IMPORT_ERROR = None
_probed = False

# +Y is north, always. Ladybug measures north as a clockwise angle from +Y, so
# with that convention the offset is zero and no input is needed.
NORTH_ANGLE = 0.0


def ensure(force=False):
    """Bind whatever Ladybug packages are installed. Cheap once it succeeds."""
    global core, geometry, comfort, radiance, AVAILABLE, IMPORT_ERROR, _probed
    if _probed and not force:
        return AVAILABLE

    _probed = True
    try:
        import ladybug
        import ladybug.analysisperiod
        import ladybug.color
        import ladybug.compass
        import ladybug.datacollection
        import ladybug.epw
        import ladybug.legend
        import ladybug.sunpath
        import ladybug_geometry.geometry2d as lbg2
        import ladybug_geometry.geometry3d as lbg3

        core = ladybug
        geometry = type("LBGeometry", (), {"g2": lbg2, "g3": lbg3})
        AVAILABLE = True
        IMPORT_ERROR = None
    except Exception:
        AVAILABLE = False
        IMPORT_ERROR = traceback.format_exc()
        return False

    try:
        import ladybug_comfort

        comfort = ladybug_comfort
    except Exception:
        comfort = None

    try:
        import ladybug_radiance

        radiance = ladybug_radiance
    except Exception:
        radiance = None

    return True


def status():
    """Diagnostic. From the Blender console:
    import refops_api; print(refops_api.ladybug_helpers.status())"""
    ensure()
    return {
        "AVAILABLE": AVAILABLE,
        "core": getattr(core, "__version__", None) if core else None,
        "comfort": comfort is not None,
        "radiance": radiance is not None,
        "IMPORT_ERROR": IMPORT_ERROR,
    }


def optional(module_path, attribute=None):
    """Import a Ladybug submodule that may not exist in this version.

    Returns None instead of raising, so a graph whose backing class was renamed
    or never shipped degrades to a console warning — the rest of the operator
    keeps working."""
    try:
        module = __import__(module_path, fromlist=["_"])
    except Exception as e:
        print(f"[RefOps.ladybug] '{module_path}' indisponível: {e}")
        return None
    if attribute is None:
        return module
    member = getattr(module, attribute, None)
    if member is None:
        print(f"[RefOps.ladybug] '{module_path}.{attribute}' não existe nesta versão.")
    return member


# --- SPATIAL REFERENCE (the parent object) ---


class Reference:
    """The parent object read as a spatial frame.

    Everything a graph needs to place itself: origin, local bounding box, and
    the parent's own transform. Charts are laid out in the parent's LOCAL space
    and the object carrying them inherits the parent's matrix, so rotating or
    moving the reference carries its graphs along."""

    def __init__(self, obj):
        self.obj = obj
        self.matrix = obj.matrix_world.copy()
        corners = [v[:] for v in obj.bound_box] if obj.data else []
        if corners and any(any(c) for c in corners):
            self.min = tuple(min(c[i] for c in corners) for i in range(3))
            self.max = tuple(max(c[i] for c in corners) for i in range(3))
        else:
            # An Empty has no bounding box at all. Its display size is the only
            # dimension it carries, and it is the natural thing to read — a
            # 1-metre fallback made every dome come out ankle-high.
            extent = getattr(obj, "empty_display_size", 0.0) or 1.0
            self.min = (-extent, -extent, 0.0)
            self.max = (extent, extent, extent)

    @property
    def width(self):
        return max(self.max[0] - self.min[0], 1e-6)

    @property
    def depth(self):
        return max(self.max[1] - self.min[1], 1e-6)

    @property
    def height(self):
        return max(self.max[2] - self.min[2], 1e-6)

    @property
    def base_point_2d(self):
        """Lower-left of the bounding box — where a 2D chart starts."""
        return self.min[0], self.min[1]

    def dimensions(self, mode="XY"):
        """Chart extents, honouring which axes should follow the parent.

        A psychrometric chart squeezed into a wide, shallow reference comes out
        unreadable; locking one axis lets the parent drive proportion on the
        other while the chart keeps its natural aspect on that one."""
        width, depth = self.width, self.depth
        if mode == "X":
            return width, width
        if mode == "Y":
            return depth, depth
        if mode == "NONE":
            return None, None
        return width, depth

    def radius(self, scale=1.0):
        """Radius for a dome or a rose: half the smaller plan dimension, so the
        graph fits inside the reference footprint."""
        return 0.5 * min(self.width, self.depth) * max(scale, 1e-6)


# --- GEOMETRY CONVERSION ---


def mesh_from_lb(name, lb_mesh, values=None, attribute="value"):
    """ladybug_geometry Mesh3D or Mesh2D → Blender mesh.

    `values` is one number per face; it is stored as a face attribute so a
    display material can drive a Color Ramp from it. That is the whole point of
    the attribute — the numbers stay in the geometry instead of being baked into
    vertex colours."""
    vertices = []
    for vertex in lb_mesh.vertices:
        z = getattr(vertex, "z", 0.0)
        vertices.append((vertex.x, vertex.y, z))
    faces = [list(face) for face in lb_mesh.faces]

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()

    # Ladybug's charts colour their own meshes. Baking those colours into a
    # corner attribute means the graph reads correctly with no material at all,
    # and a display material can still override it.
    lb_colors = getattr(lb_mesh, "colors", None)
    if lb_colors:
        _apply_vertex_colors(mesh, lb_mesh, lb_colors)

    if values is not None and mesh.polygons:
        layer = mesh.attributes.new(name=attribute, type="FLOAT", domain="FACE")
        padded = list(values)[: len(mesh.polygons)]
        padded += [0.0] * (len(mesh.polygons) - len(padded))
        layer.data.foreach_set("value", padded)
        mesh.update()
    return mesh


def curve_from_lb(name, lb_curves, cyclic=False):
    """Polylines, line segments, arcs and rings → one Blender curve object.

    Accepts a single object or a sequence, and reads whichever point property
    the Ladybug type exposes — Arc3D has no `.vertices`, so it is subdivided."""
    if not isinstance(lb_curves, (list, tuple, set)):
        lb_curves = [lb_curves]

    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    added = 0

    for item in lb_curves:
        points = _points_of(item)
        if len(points) < 2:
            continue
        spline = curve.splines.new("POLY")
        spline.points.add(len(points) - 1)
        for index, point in enumerate(points):
            spline.points[index].co = (point[0], point[1], point[2], 1.0)
        spline.use_cyclic_u = cyclic
        added += 1

    if not added:
        bpy.data.curves.remove(curve)
        return None
    return curve


def _points_of(item, subdivisions=64):
    if hasattr(item, "vertices"):
        return [(p.x, p.y, getattr(p, "z", 0.0)) for p in item.vertices]

    # Arcs BEFORE the two-point test. An Arc3D also exposes p1 and p2, so
    # checking those first turned every monthly day arc into a straight chord —
    # which is exactly how they came out on the spherical sun path.
    for method in ("subdivide_evenly", "subdivide"):
        sampler = getattr(item, method, None)
        if sampler is None:
            continue
        try:
            points = list(sampler(subdivisions))
        except Exception:
            continue
        if len(points) > 2:
            return [(p.x, p.y, getattr(p, "z", 0.0)) for p in points]

    if hasattr(item, "p1") and hasattr(item, "p2"):
        return [
            (item.p1.x, item.p1.y, getattr(item.p1, "z", 0.0)),
            (item.p2.x, item.p2.y, getattr(item.p2, "z", 0.0)),
        ]
    if hasattr(item, "boundary"):
        return [(p.x, p.y, getattr(p, "z", 0.0)) for p in item.boundary]
    return []


def points_object(name, lb_points, radius=0.0):
    """Sun positions and the like. radius 0 gives a vertex cloud, which renders
    as points and stays cheap for a full year of suns."""
    coords = [(p.x, p.y, getattr(p, "z", 0.0)) for p in lb_points]
    if not coords:
        return None
    mesh = bpy.data.meshes.new(name)
    if radius <= 0:
        mesh.from_pydata(coords, [], [])
    else:
        vertices, faces = [], []
        for centre in coords:
            base = len(vertices)
            for i in range(6):
                angle = 2 * math.pi * i / 6
                vertices.append(
                    (
                        centre[0] + radius * math.cos(angle),
                        centre[1] + radius * math.sin(angle),
                        centre[2],
                    )
                )
            faces.append(list(range(base, base + 6)))
        mesh.from_pydata(vertices, [], faces)
    mesh.update()
    return mesh


def text_object(name, body, location, size=1.0, align="CENTER"):
    curve = bpy.data.curves.new(name, type="FONT")
    curve.body = str(body)
    curve.size = size
    curve.align_x = align
    curve.align_y = "CENTER"
    obj = bpy.data.objects.new(name, curve)
    # The caller's matrix is applied to every object added to a GraphOutput, so
    # a location written here would be overwritten. It is carried on the object
    # instead and folded into the matrix at insertion time.
    obj["refops_local_location"] = tuple(location)
    return obj


# --- LEGEND ---


def legend_objects(prefix, lb_legend, text_size=None):
    """A Ladybug Legend rendered as mesh plus text.

    The legend keeps Ladybug's own colours: they encode the value scale, so
    overriding them with a display material would make the legend disagree with
    what it is labelling."""
    objects = []
    if lb_legend is None:
        return objects

    try:
        segment_mesh = lb_legend.segment_mesh
    except Exception as e:
        print(f"[RefOps.ladybug] legenda sem malha: {e}")
        return objects

    # The legend's own segment values, one per face, so the same display
    # material paints the legend and the chart with the same ramp — otherwise
    # the key disagrees with the thing it is keying.
    values = None
    try:
        numbers = [
            number
            for number in lb_legend.segment_numbers
            if isinstance(number, (int, float))
        ]
        faces = len(segment_mesh.faces)
        if numbers and faces:
            # segment_numbers marks the BOUNDARIES between bands; a band's value
            # is the midpoint of the pair that encloses it.
            if len(numbers) >= faces + 1:
                values = [
                    (numbers[i] + numbers[i + 1]) / 2.0 for i in range(faces)
                ]
            else:
                values = list(numbers[:faces])
    except Exception as e:
        print(f"[RefOps.ladybug] valores da legenda: {e}")

    mesh = mesh_from_lb(f"{prefix}_legend", segment_mesh, values)
    colors = getattr(lb_legend, "segment_colors", None)
    if colors:
        _apply_vertex_colors(mesh, segment_mesh, colors)
    objects.append(bpy.data.objects.new(f"{prefix}_legend", mesh))

    try:
        labels = lb_legend.segment_text
        points = lb_legend.segment_text_location
        if not labels:
            # Some charts leave segment_text empty; the numbers are still there.
            labels = [f"{n:g}" for n in lb_legend.segment_numbers]
        size = text_size or lb_legend.legend_parameters.text_height
        for label, plane in zip(labels, points):
            origin = getattr(plane, "o", plane)
            objects.append(
                text_object(
                    f"{prefix}_legend_txt",
                    label,
                    (origin.x, origin.y, getattr(origin, "z", 0.0)),
                    size,
                    align="LEFT",
                )
            )
        title = getattr(lb_legend, "title", None)
        if title:
            plane = lb_legend.title_location
            origin = getattr(plane, "o", plane)
            objects.append(
                text_object(
                    f"{prefix}_legend_title",
                    title,
                    (origin.x, origin.y, getattr(origin, "z", 0.0)),
                    size,
                    align="LEFT",
                )
            )
    except Exception as e:
        print(f"[RefOps.ladybug] rótulos da legenda: {e}")
    return objects


def apply_face_colors(mesh, colors):
    """Ladybug colours (one per face) → a Blender corner colour attribute."""
    if not mesh.polygons or not colors:
        return
    try:
        layer = mesh.color_attributes.get("lb_color") or mesh.color_attributes.new(
            name="lb_color", type="FLOAT_COLOR", domain="CORNER"
        )
    except Exception:
        return
    flat = []
    for index, polygon in enumerate(mesh.polygons):
        color = colors[index] if index < len(colors) else colors[-1]
        rgba = (color.r / 255.0, color.g / 255.0, color.b / 255.0, 1.0)
        flat.extend(rgba * len(polygon.vertices))
    layer.data.foreach_set("color", flat)


def set_face_values(mesh, values, attribute="value"):
    """One number per face, for a display material's Color Ramp."""
    if not mesh or not mesh.polygons or not values:
        return None
    layer = mesh.attributes.get(attribute)
    if layer is not None and (layer.data_type != "FLOAT" or layer.domain != "FACE"):
        mesh.attributes.remove(layer)
        layer = None
    if layer is None:
        layer = mesh.attributes.new(name=attribute, type="FLOAT", domain="FACE")
    padded = list(values)[: len(mesh.polygons)]
    padded += [0.0] * (len(mesh.polygons) - len(padded))
    layer.data.foreach_set("value", padded)
    mesh.update()
    return layer


def build_legend(values, title=None, origin=(0.0, 0.0, 0.0), height=1.0):
    """A standalone Legend for values that aren't already backed by a chart.

    Charts build their own legend and place it themselves. A sun path does not —
    the suns are just points — so colouring them by a climate variable means
    constructing the legend by hand and telling it where to stand, which is what
    base_plane does."""
    from ladybug.legend import Legend, LegendParameters
    from ladybug_geometry.geometry3d.plane import Plane
    from ladybug_geometry.geometry3d.pointvector import Point3D

    parameters = LegendParameters()
    if title:
        parameters.title = title
    parameters.base_plane = Plane(o=Point3D(*origin))
    parameters.segment_height = height / 10.0
    parameters.segment_width = height / 20.0
    parameters.text_height = height / 25.0
    return Legend(list(values), parameters)


def _apply_vertex_colors(mesh, lb_mesh, colors):
    """Ladybug colours are per FACE; Blender colour attributes are per corner."""
    if not mesh.polygons:
        return
    try:
        layer = mesh.color_attributes.new(
            name="lb_color", type="FLOAT_COLOR", domain="CORNER"
        )
    except Exception:
        return
    flat = []
    for index, polygon in enumerate(mesh.polygons):
        color = colors[index] if index < len(colors) else colors[-1]
        rgba = (color.r / 255.0, color.g / 255.0, color.b / 255.0, 1.0)
        flat.extend(rgba * len(polygon.vertices))
    layer.data.foreach_set("color", flat)


# --- OUTPUT ---


class GraphOutput:
    """Collects everything a graph produced and files it in one collection.

    The collection is the operator's output, so it also carries the metadata:
    which operator made it, from which EPW, over which period."""

    def __init__(self, name, parent_obj, matrix, metadata=None):
        self.collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(self.collection)
        self.matrix = matrix
        self.parent_obj = parent_obj
        self.scale = 1.0
        self.objects = []
        self.metrics = {}
        self.collection["refops_operator"] = "ladybug_graphs"
        self.collection["refops_parent"] = parent_obj.name
        for key, value in (metadata or {}).items():
            if value is not None:
                self.collection[key] = value

    def add(self, obj, material=None, values_attribute=None, scaled=True):
        if obj is None:
            return None
        self.collection.objects.link(obj)
        matrix = self.matrix
        # Text opts out of the graph scale. A wind rose is drawn at its own size
        # and then scaled to the reference, and that dragged its labels along —
        # the type came out a different size from every other chart's.
        if self.scale != 1.0 and scaled:
            matrix = matrix @ mathutils.Matrix.Scale(self.scale, 4)
        local = obj.get("refops_local_location")
        if local is not None:
            # Meshes and curves carry their coordinates in the geometry; text
            # objects carry theirs on the object, so the offset is folded in
            # here instead of being flattened by the shared matrix.
            matrix = matrix @ mathutils.Matrix.Translation(tuple(local))
            del obj["refops_local_location"]
        obj.matrix_world = matrix
        if material is not None and hasattr(obj.data, "materials"):
            obj.data.materials.append(material)
        if values_attribute:
            obj["refops_value_attribute"] = values_attribute
        self.objects.append(obj)
        return obj

    def add_mesh(self, name, mesh, material=None, values_attribute=None):
        if mesh is None:
            return None
        return self.add(
            bpy.data.objects.new(name, mesh), material, values_attribute
        )

    def add_curve(self, name, lb_curves, cyclic=False):
        curve = curve_from_lb(name, lb_curves, cyclic)
        if curve is None:
            return None
        return self.add(bpy.data.objects.new(name, curve))

    def add_text(self, name, body, location, size=1.0, align="CENTER", scaled=True):
        return self.add(
            text_object(name, body, location, size, align), scaled=scaled
        )

    def add_legend(self, prefix, lb_legend, text_size=None, material=None):
        for obj in legend_objects(prefix, lb_legend, text_size):
            self.add(obj, material if obj.type == "MESH" else None)

    def to_grease_pencil(self, thickness=5.0):
        """Convert the curve objects to grease pencil, leaving meshes alone.

        Curves are the chart's LINES — axes, analemmas, borders — and a drawn
        weight is what keeps them readable at any zoom. Meshes carry the data
        and its colours, and converting those would throw the values away."""
        curves = [obj for obj in self.objects if obj.type == "CURVE"]
        if not curves:
            return 0
        converted = 0
        for obj in curves:
            try:
                bpy.ops.object.select_all(action="DESELECT")
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.convert(target="GREASEPENCIL")
                new_obj = bpy.context.view_layer.objects.active
                bpy.ops.object.modifier_add(type="GREASE_PENCIL_THICKNESS")
                modifier = new_obj.modifiers[-1]
                for name in ("thickness_factor", "thickness"):
                    if hasattr(modifier, name):
                        setattr(modifier, name, thickness)
                        break
                converted += 1
            except Exception as e:
                print(f"[RefOps.ladybug] grease pencil '{obj.name}': {e}")
        return converted

    def record(self, **metrics):
        """Numbers worth reading back downstream — sunlit hours, extremes,
        averages. They land on the collection AND on the container object, which
        is what a later operation or a driver actually has a handle on."""
        for key, value in metrics.items():
            if value is None:
                continue
            self.metrics[key] = value
            self.collection[key] = value

    def publish(self, container_obj):
        for key, value in self.metrics.items():
            container_obj[key] = value
        container_obj["refops_metrics"] = ",".join(sorted(self.metrics))


def cleanup_collections(parent_name, operator_id="ladybug_graphs"):
    """Remove what a previous run of this operator made for this parent.

    Collections are matched on the custom properties stamped at creation rather
    than on names, which the user is free to change."""
    for collection in list(bpy.data.collections):
        try:
            if collection.get("refops_operator") != operator_id:
                continue
            if collection.get("refops_parent") != parent_name:
                continue
        except ReferenceError:
            continue
        for obj in list(collection.objects):
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            _remove_data(data)
        bpy.data.collections.remove(collection)


def _remove_data(data):
    if data is None:
        return
    try:
        if data.users or data.use_fake_user:
            return
        for store in (bpy.data.meshes, bpy.data.curves):
            if data.rna_type.identifier in {"Mesh", "Curve", "TextCurve"}:
                try:
                    store.remove(data)
                    return
                except (TypeError, RuntimeError):
                    continue
    except (ReferenceError, AttributeError):
        pass


# --- BLENDER GEOMETRY → LADYBUG ---


def faces_from_object(obj, selected_only=None):
    """Blender mesh → list of (Face3D, polygon index).

    Triangulation is deliberately skipped: Face3D takes an n-gon boundary
    directly, and keeping one Face3D per Blender polygon is what lets results
    map back to face indices without a lookup table.

    selected_only, when given, is a set of polygon indices; everything else is
    still returned for the OBSTRUCTION pass but flagged so the caller can tell
    analysis faces from mere geometry."""
    from ladybug_geometry.geometry3d.face import Face3D
    from ladybug_geometry.geometry3d.pointvector import Point3D

    mesh = getattr(obj, "data", None)
    if not mesh or not getattr(mesh, "polygons", None):
        return []

    matrix = obj.matrix_world
    result = []
    for polygon in mesh.polygons:
        if selected_only is not None and polygon.index not in selected_only:
            continue
        points = []
        for vertex_index in polygon.vertices:
            world = matrix @ mesh.vertices[vertex_index].co
            points.append(Point3D(world.x, world.y, world.z))
        if len(points) < 3:
            continue
        try:
            result.append((Face3D(points), polygon.index))
        except Exception:
            continue  # degenerate polygon
    return result


def collection_meshes(collection):
    """Visible mesh objects of a collection and its children.

    all_objects already walks nested collections, which is what makes the
    context recursive without any traversal here."""
    if collection is None:
        return []
    return [
        obj
        for obj in collection.all_objects
        if obj.type == "MESH" and obj.visible_get()
    ]


def collection_faces(collection):
    """Every mesh face in a collection and its children, in world space."""
    faces = []
    for obj in collection_meshes(collection):
        faces.extend(face for face, _index in faces_from_object(obj))
    return faces


def write_vertex_group(obj, name, values, indices=None):
    """Results as vertex-group weights.

    Weights are clamped to 0..1, so the values are normalised over the run and
    the group carries the SHAPE of the distribution, not its magnitude. The real
    numbers stay in the float attribute; a group is for driving modifiers and
    selections, which is what weights are good at."""
    # hasattr, NOT truthiness: vertex_groups is a bpy_prop_collection and an
    # EMPTY one is falsy, so the old test refused to write on exactly the object
    # that has no group yet — the first run on any object, silently.
    if obj is None or not hasattr(obj, "vertex_groups"):
        return None
    numbers = list(values)
    if not numbers:
        return None
    low, high = min(numbers), max(numbers)
    span = (high - low) or 1.0

    group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
    targets = indices if indices is not None else range(len(numbers))
    for position, index in enumerate(targets):
        if position >= len(numbers):
            break
        weight = (numbers[position] - low) / span
        group.add([index], max(0.0, min(1.0, weight)), "REPLACE")
    obj["refops_group_range"] = (round(low, 4), round(high, 4))
    return group


def vertex_sensors(obj, offset=0.01):
    """One sample point and normal per vertex, lifted off the surface.

    The vertex normal is the average of its faces', so a sample on a corner
    looks outward rather than along one arbitrary face."""
    from ladybug_geometry.geometry3d.pointvector import Point3D, Vector3D

    mesh = getattr(obj, "data", None)
    if not mesh or not getattr(mesh, "vertices", None):
        return []
    matrix = obj.matrix_world
    rotation = matrix.to_3x3()
    sensors = []
    for vertex in mesh.vertices:
        world = matrix @ vertex.co
        normal = (rotation @ vertex.normal).normalized()
        origin = Point3D(
            world.x + normal.x * offset,
            world.y + normal.y * offset,
            world.z + normal.z * offset,
        )
        sensors.append((vertex.index, origin, Vector3D(normal.x, normal.y, normal.z)))
    return sensors


def face_sensors(obj, indices, offset=0.01):
    """One sample point and normal per analysed face.

    The point is lifted off the surface along its own normal — a ray starting
    exactly on the face it belongs to hits that face at t=0 and every result
    comes back zero."""
    from ladybug_geometry.geometry3d.pointvector import Point3D, Vector3D

    mesh = obj.data
    matrix = obj.matrix_world
    rotation = matrix.to_3x3()
    sensors = []
    for index in indices:
        polygon = mesh.polygons[index]
        centre = matrix @ polygon.center
        normal = (rotation @ polygon.normal).normalized()
        origin = Point3D(
            centre.x + normal.x * offset,
            centre.y + normal.y * offset,
            centre.z + normal.z * offset,
        )
        sensors.append((index, origin, Vector3D(normal.x, normal.y, normal.z)))
    return sensors


def is_blocked(origin, direction, obstacles, distance=1e6):
    """True when a ray from origin along direction meets any obstacle.

    Face3D.intersect_line_ray is Ladybug's own test, so the result agrees with
    the rest of the library. It is pure Python and linear in the obstacle count;
    is_blocked_bvh answers the same question through a BVH and is what the
    operators call. This one stays as the reference the BVH is checked against,
    and as the path for obstacles that only exist as Face3D."""
    from ladybug_geometry.geometry3d.ray import Ray3D

    ray = Ray3D(origin, direction)
    for face in obstacles:
        try:
            if face.intersect_line_ray(ray) is not None:
                return True
        except Exception:
            continue
    return False


# --- OBSTRUCTION: BVH ---


def obstacle_bvh(objects, epsilon=0.0):
    """World-space BVH over every mesh face of `objects`, or None if empty.

    The geometry fed in is the same one the Face3D obstacle list carried: the
    mesh's own vertices through matrix_world, each polygon as its n-gon index
    tuple. FromPolygons fans an n-gon into triangles, so the surface tested is
    the same boundary Face3D would have tested; what changes is only HOW a ray
    finds a candidate — a tree walk instead of a linear scan over every face.

    epsilon stays at 0.0 on purpose: any padding would grow the obstacles and
    start blocking rays that used to pass.
    """
    from mathutils.bvhtree import BVHTree

    vertices = []
    polygons = []
    for obj in objects:
        mesh = getattr(obj, "data", None)
        if not mesh or not getattr(mesh, "polygons", None):
            continue
        matrix = obj.matrix_world
        offset = len(vertices)
        vertices.extend((matrix @ vertex.co)[:] for vertex in mesh.vertices)
        for polygon in mesh.polygons:
            if len(polygon.vertices) < 3:
                continue  # degenerate, same ones Face3D refused
            polygons.append([offset + index for index in polygon.vertices])
    if not polygons:
        return None
    try:
        return BVHTree.FromPolygons(
            vertices, polygons, all_triangles=False, epsilon=epsilon
        )
    except Exception as e:
        print(f"[RefOps.ladybug] BVH das obstruções: {e}")
        return None


def as_vector(point):
    """Ladybug Point3D/Vector3D → mathutils.Vector, which is what the BVH takes.

    Callers hoist this out of the inner loop — the ray count is
    sensors x vectors and the conversion would otherwise dominate the win."""
    from mathutils import Vector

    return Vector((point.x, point.y, point.z))


def is_blocked_bvh(bvh, origin, direction, distance=1e6):
    """is_blocked's answer, from a BVH: does this ray meet any obstacle?

    origin and direction are mathutils Vectors (see as_vector). Only whether
    there is a hit matters, so ray_cast's nearest-hit is already the answer.
    An empty tree (None) obstructs nothing, matching an empty obstacle list.

    The sensor sits offset off its own face — see face_sensors — which is what
    keeps a face from shadowing itself here just as it did with Face3D."""
    if bvh is None:
        return False
    return bvh.ray_cast(origin, direction, distance)[0] is not None


# --- DATA ---


def load_epw(path):
    if not path:
        print("[RefOps.ladybug] Nenhum arquivo EPW informado.")
        return None
    resolved = bpy.path.abspath(path)
    try:
        from ladybug.epw import EPW

        return EPW(resolved)
    except Exception as e:
        print(f"[RefOps.ladybug] EPW '{resolved}': {e}")
        return None


def analysis_period(holder):
    """AnalysisPeriod from the operator's period inputs."""
    from ladybug.analysisperiod import AnalysisPeriod

    mode = getattr(holder, "period_mode", "YEAR")
    if mode == "YEAR":
        return AnalysisPeriod()
    if mode == "DAY":
        return AnalysisPeriod(
            st_hour=int(getattr(holder, "start_hour", 0)),
            end_hour=int(getattr(holder, "end_hour", 23)),
        )
    try:
        return AnalysisPeriod(
            st_month=int(holder.start_month),
            st_day=int(holder.start_day),
            st_hour=int(holder.start_hour),
            end_month=int(holder.end_month),
            end_day=int(holder.end_day),
            end_hour=int(holder.end_hour),
        )
    except Exception as e:
        print(f"[RefOps.ladybug] Período inválido ({e}); usando o ano inteiro.")
        return AnalysisPeriod()


def epw_field(epw, key):
    """Named EPW variable → hourly data collection."""
    attribute = {
        "DRY_BULB": "dry_bulb_temperature",
        "DEW_POINT": "dew_point_temperature",
        "REL_HUMIDITY": "relative_humidity",
        "WIND_SPEED": "wind_speed",
        "WIND_DIRECTION": "wind_direction",
        "DIRECT_NORMAL": "direct_normal_radiation",
        "DIFFUSE_HORIZONTAL": "diffuse_horizontal_radiation",
        "GLOBAL_HORIZONTAL": "global_horizontal_radiation",
        "ILLUMINANCE": "global_horizontal_illuminance",
        "SKY_COVER": "total_sky_cover",
    }.get(key)
    if not attribute:
        return None
    data = getattr(epw, attribute, None)
    if data is None:
        print(f"[RefOps.ladybug] EPW sem '{attribute}' nesta versão.")
    return data


def apply_period(data, period):
    if data is None or period is None:
        return data
    try:
        return data.filter_by_analysis_period(period)
    except Exception:
        return data


def legend_parameters(holder, title=None):
    from ladybug.legend import LegendParameters

    parameters = LegendParameters()
    if title:
        parameters.title = title
    return parameters
