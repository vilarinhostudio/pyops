"""Blender → Honeybee → Radiance bridge.

Lives in the addon so the planned artificial-lighting and IES work can share it:

    import pyops_api
    hb = pyops_api.honeybee_helpers

Honeybee is resolved on FIRST USE, for the same reason as Bonsai and Ladybug —
the packages may be installed into Blender's Python after the addon registered,
and Python caches a failed module-level import for the whole session.

Two dependencies, not one. The Python packages come from pip; the Radiance
BINARIES do not. A daylight recipe with honeybee installed and Radiance missing
fails deep inside a subprocess, so both are probed separately and reported
separately.
"""

import math
import os
import shutil
import traceback

import bpy
import mathutils

honeybee = None
honeybee_radiance = None
recipes = None

AVAILABLE = False
RECIPES_AVAILABLE = False
IMPORT_ERROR = None
_probed = False


def ensure(force=False):
    global honeybee, honeybee_radiance, recipes
    global AVAILABLE, RECIPES_AVAILABLE, IMPORT_ERROR, _probed

    if _probed and not force:
        return AVAILABLE
    _probed = True

    try:
        import honeybee.face
        import honeybee.model
        import honeybee.room
        import honeybee_radiance.sensorgrid
        import honeybee.aperture
        import honeybee.door

        honeybee = __import__("honeybee")
        honeybee_radiance = __import__("honeybee_radiance")
        AVAILABLE = True
        IMPORT_ERROR = None
    except Exception:
        AVAILABLE = False
        IMPORT_ERROR = traceback.format_exc()
        return False

    try:
        import lbt_recipes.recipe

        recipes = lbt_recipes.recipe
        RECIPES_AVAILABLE = True
    except Exception as e:
        recipes = None
        RECIPES_AVAILABLE = False
        print(f"[PyOps.honeybee] lbt-recipes indisponível: {e}")

    return True


def radiance_path():
    """Folder holding the Radiance executables, or None.

    Checked by looking for the binaries themselves rather than for the package:
    honeybee-radiance installs happily without Radiance, and the failure then
    surfaces as an opaque subprocess error halfway through a recipe."""
    for command in ("rtrace", "rpict", "gendaymtx"):
        found = shutil.which(command)
        if found:
            return os.path.dirname(found)
    for variable in ("RAYPATH", "RADIANCE_PATH"):
        value = os.environ.get(variable)
        if value:
            first = value.split(os.pathsep)[0]
            if os.path.isdir(first):
                return first
    return None


def status():
    """From the Blender console:
    import pyops_api; print(pyops_api.honeybee_helpers.status())"""
    ensure()
    return {
        "AVAILABLE": AVAILABLE,
        "RECIPES_AVAILABLE": RECIPES_AVAILABLE,
        "RADIANCE": radiance_path(),
        "IMPORT_ERROR": IMPORT_ERROR,
    }


def optional(module_path, attribute=None):
    try:
        module = __import__(module_path, fromlist=["_"])
    except Exception as e:
        print(f"[PyOps.honeybee] '{module_path}' indisponível: {e}")
        return None
    if attribute is None:
        return module
    member = getattr(module, attribute, None)
    if member is None:
        print(f"[PyOps.honeybee] '{module_path}.{attribute}' não existe.")
    return member


# --- MODEL CONSTRUCTION ---


def _face_type(kind):
    from honeybee.facetype import Floor, RoofCeiling, Wall

    return {"WALL": Wall(), "FLOOR": Floor(), "ROOF": RoofCeiling()}.get(kind, Wall())


def classify_faces(obj, attributes):
    """Face index → 'WALL' | 'FLOOR' | 'ROOF' | 'APERTURE' | 'DOOR'.

    Attribute names are read as BOOL face layers. A face flagged by more than
    one attribute is resolved by precedence: an opening is an opening even if
    the wall attribute also covers it, which is how these meshes are usually
    painted — the whole wall marked, then the window marked on top."""
    mesh = getattr(obj, "data", None)
    if not mesh or not getattr(mesh, "polygons", None):
        return {}

    count = len(mesh.polygons)
    flags = {}
    for kind, name in attributes.items():
        if not name:
            continue
        attribute = mesh.attributes.get(name)
        if attribute is None:
            print(f"[PyOps.honeybee] Atributo '{name}' não existe.")
            continue
        if attribute.domain != "FACE" or attribute.data_type != "BOOLEAN":
            print(
                f"[PyOps.honeybee] '{name}' é {attribute.data_type}/"
                f"{attribute.domain}; precisa ser BOOLEAN/FACE."
            )
            continue
        values = [False] * count
        attribute.data.foreach_get("value", values)
        flags[kind] = values

    classified = {}
    for index in range(count):
        for kind in ("APERTURE", "DOOR", "ROOF", "FLOOR", "WALL"):
            if flags.get(kind) and flags[kind][index]:
                classified[index] = kind
                break
    return classified


def model_from_object(obj, attributes, context_collection=None, identifier="pyops"):
    """A Honeybee Model built from the object's faces and their attributes.

    Opaque faces become orphaned Faces; openings become Apertures attached to
    the coplanar wall that contains them, falling back to orphaned Apertures.
    Context geometry becomes Shades — it blocks light without being part of the
    building.

    Orphaned faces rather than Rooms: a Room needs a closed solid, and an
    architectural mesh painted with attributes rarely is one. The sensor grid is
    supplied separately, so nothing here depends on having Rooms."""
    from honeybee.aperture import Aperture
    from honeybee.door import Door
    from honeybee.face import Face
    from honeybee.model import Model
    from honeybee.shade import Shade

    import pyops_api

    lb = pyops_api.ladybug_helpers

    classified = classify_faces(obj, attributes)
    if not classified:
        print("[PyOps.honeybee] Nenhuma face classificada pelos atributos.")
        return None

    geometry = {index: face for face, index in lb.faces_from_object(obj)}

    faces, apertures, doors = [], [], []
    for index, kind in classified.items():
        face3d = geometry.get(index)
        if face3d is None:
            continue
        name = f"{identifier}_{kind.lower()}_{index}"
        if kind == "APERTURE":
            apertures.append(Aperture(name, face3d))
        elif kind == "DOOR":
            doors.append(Door(name, face3d))
        else:
            faces.append(Face(name, face3d, _face_type(kind)))

    _attach_openings(faces, apertures, doors)

    shades = []
    for position, face3d in enumerate(lb.collection_faces(context_collection)):
        shades.append(Shade(f"{identifier}_context_{position}", face3d))

    orphaned_apertures = [a for a in apertures if not a.has_parent]
    orphaned_doors = [d for d in doors if not d.has_parent]

    try:
        return Model(
            identifier,
            orphaned_faces=faces,
            orphaned_apertures=orphaned_apertures,
            orphaned_doors=orphaned_doors,
            orphaned_shades=shades,
            units="Meters",
        )
    except Exception as e:
        print(f"[PyOps.honeybee] Model: {e}")
        return None


# --- CONSTRUCTION: THE REAL ELEMENTS ---

# Two faces belong to one planar region when their normals agree within ~1° and
# they share the plane within a millimetre. IfcOpenShell meshes arrive
# triangulated, so these are what reassemble a wall face out of its triangles.
PLANE_COS = math.cos(math.radians(1.0))
PLANE_DISTANCE = 0.001

# Visible transmittance used for a void no Openings object claimed.
DEFAULT_GLAZING_VT = 0.6


def planar_regions(obj):
    """Connected coplanar faces of a mesh, as world-space outlines with holes.

    Returns [{"normal", "outer", "holes"}], points as mathutils Vectors.

    A Blender polygon has exactly one boundary loop — there is no such thing as
    a face with a hole. A void cut through a wall shows up instead as faces
    AROUND the hole, so holes only exist at the level of the region: the
    region's boundary edges chain into loops, the largest is the outline and
    every other loop is a void. The reveal faces inside the void are not
    coplanar, which is what closes that inner loop."""
    import bmesh

    mesh = getattr(obj, "data", None)
    if mesh is None or not getattr(mesh, "polygons", None):
        return []

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.transform(obj.matrix_world)
        bm.normal_update()

        visited = set()
        regions = []
        for seed in bm.faces:
            # A zero-length normal is a degenerate face: it can't define a plane.
            if seed in visited or seed.normal.length < 0.5:
                continue
            normal = seed.normal.copy()
            origin = seed.verts[0].co.copy()
            region = {seed}
            visited.add(seed)
            stack = [seed]
            while stack:
                face = stack.pop()
                for edge in face.edges:
                    for other in edge.link_faces:
                        if other in visited or other.normal.dot(normal) < PLANE_COS:
                            continue
                        if abs((other.verts[0].co - origin).dot(normal)) > PLANE_DISTANCE:
                            continue
                        visited.add(other)
                        region.add(other)
                        stack.append(other)

            loops = _boundary_loops(region)
            if not loops:
                continue
            loops.sort(key=lambda points: _loop_area(points, normal), reverse=True)
            regions.append({"normal": normal, "outer": loops[0], "holes": loops[1:]})
        return regions
    finally:
        bm.free()


def _boundary_loops(faces):
    """Closed point loops along the edges a region does not share internally.

    Edges are taken in the direction their face winds, so every loop comes out
    consistently oriented: the outline counter-clockwise about the normal, the
    holes clockwise."""
    following = {}
    for face in faces:
        for loop in face.loops:
            inside = sum(1 for other in loop.edge.link_faces if other in faces)
            if inside == 1:
                following.setdefault(loop.vert, []).append(loop.link_loop_next.vert)

    loops = []
    while following:
        start = next(iter(following))
        points = []
        vert = start
        while vert in following:
            points.append(vert.co.copy())
            ends = following[vert]
            nxt = ends.pop()
            if not ends:
                del following[vert]
            vert = nxt
            if vert == start:
                break
        if len(points) >= 3:
            loops.append(points)
    return loops


def _loop_area(points, normal):
    """Unsigned area of a planar loop (Newell)."""
    total = mathutils.Vector((0.0, 0.0, 0.0))
    for index, point in enumerate(points):
        total += point.cross(points[(index + 1) % len(points)])
    return abs(total.dot(normal)) / 2.0


def _loop_centroid(points):
    """Area-weighted centre: a triangulated boundary spaces its vertices
    unevenly, so a plain vertex average drifts towards the dense side."""
    anchor = points[0]
    weighted = mathutils.Vector((0.0, 0.0, 0.0))
    total = 0.0
    for index in range(1, len(points) - 1):
        a, b = points[index], points[index + 1]
        area = (a - anchor).cross(b - anchor).length / 2.0
        weighted += (anchor + a + b) / 3.0 * area
        total += area
    if total <= 0.0:
        return sum(points, mathutils.Vector((0.0, 0.0, 0.0))) / len(points)
    return weighted / total


def _face3d(outer, holes=None):
    from ladybug_geometry.geometry3d.face import Face3D
    from ladybug_geometry.geometry3d.pointvector import Point3D

    def convert(points):
        return [Point3D(p.x, p.y, p.z) for p in points]

    try:
        if holes:
            return Face3D(convert(outer), holes=[convert(h) for h in holes])
        return Face3D(convert(outer))
    except Exception as e:
        print(f"[PyOps.honeybee] contorno degenerado ignorado: {e}")
        return None


def pair_voids(regions, lateral_tolerance=0.01, area_tolerance=0.05):
    """Group the hole loops of one object into voids.

    A void through a wall appears twice, once on each face of the wall. Turning
    both loops into apertures would put two panes in one window, so the loops
    are paired: parallel planes, centres aligned along the normal, same area.
    A loop left without a partner is a void on its own."""
    holes = []
    for region in regions:
        normal = region["normal"]
        for points in region["holes"]:
            holes.append(
                {
                    "points": points,
                    "normal": normal,
                    "centre": _loop_centroid(points),
                    "area": _loop_area(points, normal),
                }
            )

    voids = []
    used = set()
    for i, hole in enumerate(holes):
        if i in used:
            continue
        used.add(i)
        best, best_depth = None, None
        for j, other in enumerate(holes):
            if j in used or abs(hole["normal"].dot(other["normal"])) < PLANE_COS:
                continue
            offset = other["centre"] - hole["centre"]
            along = offset.dot(hole["normal"])
            if abs(along) <= PLANE_DISTANCE:
                continue  # same plane: two different holes, not two sides
            if (offset - hole["normal"] * along).length > lateral_tolerance:
                continue
            larger = max(hole["area"], other["area"])
            if larger <= 0 or abs(hole["area"] - other["area"]) / larger > area_tolerance:
                continue
            if best_depth is None or abs(along) < best_depth:
                best, best_depth = j, abs(along)
        loops = [hole]
        if best is not None:
            used.add(best)
            loops.append(holes[best])
        voids.append({"normal": hole["normal"], "loops": loops})
    return voids


def _inside_loop(point, loop, normal, slack):
    """Point-in-polygon in the loop's plane, with a margin along the edges."""
    helper = mathutils.Vector((0.0, 0.0, 1.0))
    if abs(normal.dot(helper)) > 0.9:
        helper = mathutils.Vector((1.0, 0.0, 0.0))
    u = normal.cross(helper).normalized()
    v = normal.cross(u)
    target = mathutils.Vector((point.dot(u), point.dot(v)))
    polygon = [mathutils.Vector((p.dot(u), p.dot(v))) for p in loop]

    inside = False
    for index, a in enumerate(polygon):
        b = polygon[(index + 1) % len(polygon)]
        if (a.y > target.y) != (b.y > target.y):
            if target.x < a.x + (target.y - a.y) * (b.x - a.x) / (b.y - a.y):
                inside = not inside
    if inside:
        return True
    for index, a in enumerate(polygon):
        b = polygon[(index + 1) % len(polygon)]
        span = b - a
        ratio = 0.0 if span.length_squared == 0 else (target - a).dot(span) / span.length_squared
        closest = a + span * max(0.0, min(1.0, ratio))
        if (target - closest).length <= slack:
            return True
    return False


def _bbox_centre(obj):
    corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
    return sum(corners, mathutils.Vector((0.0, 0.0, 0.0))) / 8.0


def match_openings(voids, objects, slack=0.02):
    """{void index: object} by position, one object per void.

    The bounding-box centre is used, never the origin: a Bonsai filling keeps
    its origin on a face of the host with the host's rotation (see GOTCHAS), so
    the origin says little about where the glass is. A candidate must project
    inside the void outline and sit within the void's depth, padded the way
    the distribute operator pads a host by its thickness."""
    candidates = []
    for index, void in enumerate(voids):
        first = void["loops"][0]
        normal = void["normal"]
        depths = [(loop["centre"] - first["centre"]).dot(normal) for loop in void["loops"]]
        low, high = min(depths), max(depths)
        # A void cut in a zero-thickness surface (the EnergyPlus Room Reference)
        # has no depth of its own; the caller pads it by the wall it stands for.
        pad = void.get("pad", max(0.05, high - low))
        middle = sum((loop["centre"] for loop in void["loops"]),
                     mathutils.Vector((0.0, 0.0, 0.0))) / len(void["loops"])
        for obj in objects:
            centre = _bbox_centre(obj)
            along = (centre - first["centre"]).dot(normal)
            if not (low - pad <= along <= high + pad):
                continue
            if not _inside_loop(centre, first["points"], normal, slack):
                continue
            candidates.append(((centre - middle).length, index, obj))

    matched, taken = {}, set()
    for _distance, index, obj in sorted(candidates, key=lambda c: c[0]):
        if index in matched or obj.name in taken:
            continue
        matched[index] = obj
        taken.add(obj.name)
    return matched


def largest_outline(obj):
    """(normal, outline points) of the object's largest planar region.

    Regions rather than polygons: on a triangulated mesh the largest polygon is
    half of the pane."""
    best, best_area = None, 0.0
    for region in planar_regions(obj):
        area = _loop_area(region["outer"], region["normal"])
        if area > best_area:
            best, best_area = (region["normal"], region["outer"]), area
    return best


def glazing_values(obj, pset_name):
    """Raw glazing numbers from the Openings object's pset, None where absent.

    Shared by Radiance (VT, Reflectance) and EnergyPlus (VT, U_Factor, SHGC):
    one pset describes the window for both, so the two studies cannot disagree
    about which glass is in it."""
    import pyops_api

    bim = pyops_api.bim_helpers
    element = bim.entity(obj)
    names = {
        "vt": "VisibleTransmittance",
        "reflectance": "Reflectance",
        "u_factor": "U_Factor",
        "shgc": "SHGC",
    }
    if element is None or not pset_name:
        return {key: None for key in names}
    return {
        key: bim.pset_value(element, f"{pset_name}.{prop}")
        for key, prop in names.items()
    }


def glazing_modifier(obj, pset_name, name):
    """(modifier, origin) from the Openings object's pset.

    Radiance's glass primitive takes no reflectance — it derives it from the
    Fresnel equations — so Reflectance only matters for an opaque filling,
    which becomes plastic. Saying it was ignored is cheaper than someone
    wondering why changing it did nothing."""
    from honeybee_radiance.modifier.material import Glass, Plastic

    values = glazing_values(obj, pset_name)
    transmittance, reflectance = values["vt"], values["reflectance"]

    origin = f"{pset_name}"
    try:
        transmittance = max(0.0, min(1.0, float(transmittance)))
    except (TypeError, ValueError):
        transmittance = DEFAULT_GLAZING_VT
        origin = f"VT padrão {DEFAULT_GLAZING_VT} (sem {pset_name}.VisibleTransmittance)"

    try:
        if transmittance > 0.01:
            if reflectance is not None:
                origin += "; Reflectance ignorada (glass não usa)"
            return Glass.from_single_transmittance(name, transmittance), origin
        try:
            reflectance = max(0.0, min(1.0, float(reflectance)))
        except (TypeError, ValueError):
            reflectance = 0.5
            origin += "; Reflectance padrão 0.5"
        return Plastic.from_single_reflectance(name, reflectance), origin + " (opaco)"
    except Exception as e:
        print(f"[PyOps.honeybee] vidro '{obj.name}': {e}")
        return None, origin


def model_from_construction(construction, openings=None, context_collection=None,
                            identifier="pyops", options=None):
    """A Honeybee model built from the real walls, slabs and roofs.

    construction and openings are plain lists of mesh objects — resolved by
    the caller from a room pset, not a Collection — since one element (a
    shared wall) can belong to more than one room's list at once.

    Each object's own mesh is the surface — already carrying the voids Bonsai
    cut with apply_openings=True — so nothing is matched against a proxy
    volume. Voids are found in the topology rather than through HasOpenings,
    which proved unreliable in practice.

    Every planar region becomes an opaque Face with its holes left OPEN, and
    the glass is a separate orphaned Aperture lying in one of the two hole
    loops. Orphaned is fine here: the faces around it really are perforated,
    so light does not depend on the aperture belonging to them."""
    import pyops_api

    bim = pyops_api.bim_helpers
    lb = pyops_api.ladybug_helpers
    options = options or {}

    if not AVAILABLE:
        return None
    from honeybee.aperture import Aperture
    from honeybee.face import Face
    from honeybee.model import Model
    from honeybee.shade import Shade
    from honeybee.typing import clean_rad_string

    objects = construction or []
    if not objects:
        print("[PyOps.honeybee] Nenhum elemento de Construction marcado para este cômodo.")
        return None
    bim.ensure()

    faces, voids = [], []
    for position, obj in enumerate(objects):
        name = f"{clean_rad_string(obj.name)}_{position}"
        regions = planar_regions(obj)
        object_faces = []
        for index, region in enumerate(regions):
            face3d = _face3d(region["outer"], region["holes"])
            if face3d is None:
                continue
            try:
                object_faces.append(Face(f"{name}_f{index}", face3d))
            except Exception as e:
                print(f"[PyOps.honeybee] face de '{obj.name}': {e}")
        if not object_faces:
            print(f"[PyOps.honeybee] '{obj.name}' não gerou nenhuma face.")
            continue
        faces.extend(object_faces)

        if options.get("use_optics"):
            element = bim.entity(obj)
            if element is None:
                print(
                    f"[PyOps.honeybee] '{obj.name}' não é elemento IFC — "
                    "refletância padrão do Radiance."
                )
            else:
                _apply_modifier(object_faces, element, options)

        for void in pair_voids(regions):
            void["host"] = obj.name
            voids.append(void)

    if not faces:
        return None

    opening_objects = openings or []
    matches = match_openings(voids, opening_objects)
    pset_name = options.get("glazing_pset", "")
    report = options.get("report")

    apertures = []
    for index, void in enumerate(voids):
        obj = matches.get(index)
        loop = void["loops"][0]
        if obj is not None:
            # The pane goes on the wall face nearer to the object filling it.
            centre = _bbox_centre(obj)
            loop = min(
                void["loops"],
                key=lambda l: abs((centre - l["centre"]).dot(void["normal"])),
            )
        # Hole loops wind clockwise about their face; reversed, the pane faces
        # the same way as the wall face it sits in.
        face3d = _face3d(list(reversed(loop["points"])))
        if face3d is None:
            continue
        aperture = Aperture(f"{identifier}_void_{index}", face3d)
        if obj is None:
            c = loop["centre"]
            print(
                f"[PyOps.honeybee] Vão em '{void['host']}' ({c.x:.2f}, {c.y:.2f}, "
                f"{c.z:.2f}) sem objeto de Openings — vidro genérico."
            )
        else:
            _apply_glazing(aperture, obj, pset_name, report)
        apertures.append(aperture)

    # An Openings object no loop claimed usually fills a void that touches the
    # element's edge — a door down to the floor — which cuts a notch in the
    # outline instead of an inner loop.
    matched_names = {obj.name for obj in matches.values()}
    for position, obj in enumerate(opening_objects):
        if obj.name in matched_names:
            continue
        outline = largest_outline(obj)
        face3d = _face3d(outline[1]) if outline else None
        if face3d is None:
            continue
        print(
            f"[PyOps.honeybee] '{obj.name}' não casou com nenhum laço interno "
            "(vão encostado na borda?) — abertura pela maior face do objeto."
        )
        aperture = Aperture(
            f"{identifier}_opening_{clean_rad_string(obj.name)}_{position}", face3d
        )
        _apply_glazing(aperture, obj, pset_name, report)
        apertures.append(aperture)

    shades = []
    for position, face3d in enumerate(lb.collection_faces(context_collection)):
        shades.append(Shade(f"{identifier}_context_{position}", face3d))

    if options.get("lights") == "ROOM_PSET":
        emitters, light_report = light_shades(
            light_sources(options.get("light_objects") or [], options.get("light_pset", ""))
        )
        shades.extend(emitters)
        if light_report and report:
            print(f"[PyOps.honeybee] Luzes: {light_report}")

    try:
        return Model(
            identifier,
            orphaned_faces=faces,
            orphaned_apertures=apertures,
            orphaned_shades=shades,
            units="Meters",
        )
    except Exception as e:
        print(f"[PyOps.honeybee] Model: {e}")
        return None


def _apply_glazing(aperture, obj, pset_name, report):
    modifier, origin = glazing_modifier(obj, pset_name, f"glz_{aperture.identifier}")
    if modifier is None:
        return
    try:
        aperture.properties.radiance.modifier = modifier
    except Exception as e:
        print(f"[PyOps.honeybee] modificador de vidro não aplicado: {e}")
        return
    if report:
        print(f"[PyOps.honeybee] vidro '{obj.name}': {origin}")


def radiance_modifier(element, name, overrides=None, outermost=True, defaults=None):
    """A Radiance modifier built from the element's optical properties.

    Plastic for opaque surfaces, glass where light passes through: those are the
    two primitives Radiance actually uses, and which one applies is decided by
    the transmittance rather than by the IFC class — a glazed wall behaves like
    glass whatever it is called.

    Returns (modifier, sources) so the caller can report where each number came
    from; a value that quietly fell back to a default is the kind of thing that
    invalidates a study without anyone noticing."""
    import pyops_api

    bim = pyops_api.bim_helpers
    if not AVAILABLE:
        return None, {}

    values = bim.optical_properties(element, overrides, outermost)
    sources = {key: origin for key, (_value, origin) in values.items()}
    numbers = {key: value for key, (value, _origin) in values.items()}
    defaults = defaults or {}

    def number(key, fallback):
        raw = numbers.get(key, defaults.get(key, fallback))
        try:
            return max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            return fallback

    reflectance = number("reflectance", 0.5)
    transmittance = number("transmittance", 0.0)
    specularity = number("specularity", 0.0)
    roughness = number("roughness", 0.0)

    try:
        if transmittance > 0.01:
            from honeybee_radiance.modifier.material import Glass

            return Glass.from_single_transmittance(name, transmittance), sources
        from honeybee_radiance.modifier.material import Plastic

        return (
            Plastic.from_single_reflectance(
                name,
                reflectance,
                specularity=specularity,
                roughness=roughness,
            ),
            sources,
        )
    except Exception as e:
        print(f"[PyOps.honeybee] modificador '{name}': {e}")
        return None, sources


# Luminous efficacy used when only wattage is known. 90 lm/W is a plain LED
# fixture — enough to be honest about, and the reason a measured lumen figure
# on the fixture is worth more than this.
DEFAULT_EFFICACY = 90.0


def light_readings(objects, pset_name):
    """[(obj, LuminousFlux, Wattage)] for the fixtures, raw.

    Radiance wants lumens and EnergyPlus wants watts; each caller converts the
    one it lacks and says so."""
    import pyops_api

    bim = pyops_api.bim_helpers

    readings = []
    for obj in objects:
        element = bim.entity(obj)
        flux = watts = None
        if element is not None and pset_name:
            flux = bim.pset_value(element, f"{pset_name}.LuminousFlux")
            watts = bim.pset_value(element, f"{pset_name}.Wattage")
        readings.append((obj, flux, watts))
    return readings


def light_sources(objects, pset_name):
    """[(name, Face3D, lumens, origin)] for the fixtures.

    Flux comes from the element's pset: LuminousFlux when present, otherwise
    Wattage at a stated efficacy — an assumption, carried in the origin so it
    shows in the report. The emitter is the fixture's largest planar region."""
    sources = []
    for obj, flux, watts in light_readings(objects, pset_name):
        try:
            lumens, origin = float(flux), f"{pset_name}.LuminousFlux"
        except (TypeError, ValueError):
            try:
                lumens = float(watts) * DEFAULT_EFFICACY
                origin = f"{pset_name}.Wattage × {DEFAULT_EFFICACY:g} lm/W"
            except (TypeError, ValueError):
                print(
                    f"[PyOps.honeybee] luz '{obj.name}' sem {pset_name}.LuminousFlux "
                    "nem Wattage — ignorada."
                )
                continue

        outline = largest_outline(obj)
        if outline is None:
            continue
        normal, points = outline
        # Not verified whether a Radiance light polygon emits from its back.
        # A fixture's largest face is often its top, so a horizontal emitter is
        # turned to face down rather than light the ceiling.
        if normal.z > 0.9:
            points = list(reversed(points))
            print(f"[PyOps.honeybee] luz '{obj.name}': emissor virado para −Z.")
        face3d = _face3d(points)
        if face3d is not None:
            sources.append((obj.name, face3d, lumens, origin))
    return sources


def light_shades(sources, prefix="light"):
    """Emitting surfaces for Radiance, from light_sources().

    Each becomes a Shade carrying a `light` modifier: that is how Radiance is
    told a surface emits rather than reflects. Returns the shades and a report
    of where each flux figure came from."""
    if not AVAILABLE:
        return [], {}
    from honeybee.shade import Shade
    from honeybee_radiance.modifier.material import Light

    shades = []
    report = {}
    for index, (name, face3d, lumens, origin) in enumerate(sources):
        if lumens <= 0:
            continue
        try:
            area = max(face3d.area, 1e-4)
            # Radiance radiance, roughly: lumens spread over the emitting area
            # and over a hemisphere.
            value = lumens / (area * 179.0 * math.pi)
            modifier = Light(
                f"{prefix}_{index}_mod",
                r_emittance=value,
                g_emittance=value,
                b_emittance=value,
            )
            shade = Shade(f"{prefix}_{index}", face3d)
            shade.properties.radiance.modifier = modifier
            shades.append(shade)
            report[name] = f"{lumens:.0f} lm ({origin})"
        except Exception as e:
            print(f"[PyOps.honeybee] luz '{name}': {e}")
    return shades, report


def _apply_modifier(targets, element, options):
    """One modifier per element, shared by all its faces — optical_properties
    walks psets and materials, which is too slow to repeat per face."""
    modifier, sources = radiance_modifier(
        element,
        f"mod_{element.id()}",
        overrides=(options or {}).get("overrides"),
        outermost=(options or {}).get("outermost", True),
    )
    if modifier is None:
        return
    for target in targets:
        try:
            target.properties.radiance.modifier = modifier
        except Exception as e:
            print(f"[PyOps.honeybee] modificador não aplicado: {e}")
            return
    if not sources:
        # Printed even with Report Sources off: a study run on the 0.5 default
        # looks exactly like one run on measured values.
        print(
            f"[PyOps.honeybee] {element.is_a()} {element.id()}: nenhuma fonte "
            "óptica (Pset, material ou estilo) — refletância padrão 0.5."
        )
    elif (options or {}).get("report"):
        print(f"[PyOps.honeybee] {element.is_a()} {element.id()}: {sources}")


def _attach_openings(faces, apertures, doors, tolerance=0.01):
    """Put each opening on the face whose plane it lies in.

    Radiance needs the aperture to belong to the surface it perforates;
    an orphaned aperture floating in front of a solid wall lets no light in."""
    for opening in list(apertures) + list(doors):
        centre = opening.geometry.center
        for face in faces:
            plane = face.geometry.plane
            if abs(plane.distance_to_point(centre)) > tolerance:
                continue
            if not face.geometry.is_point_on_face(centre, tolerance):
                continue
            try:
                if opening in apertures:
                    face.add_aperture(opening)
                else:
                    face.add_door(opening)
            except Exception as e:
                print(f"[PyOps.honeybee] abertura não anexada: {e}")
            break


def sensor_grid(identifier, sensors):
    """Honeybee sensor grid from (index, Point3D, Vector3D) triples.

    The order is preserved, which is what lets results map straight back onto
    the Blender elements they came from."""
    from honeybee_radiance.sensorgrid import SensorGrid

    positions = [(point.x, point.y, point.z) for _index, point, _normal in sensors]
    directions = [
        (normal.x, normal.y, normal.z) for _index, _point, normal in sensors
    ]
    if not positions:
        return None
    return SensorGrid.from_position_and_direction(identifier, positions, directions)


# --- RECIPES ---


def run_recipe(name, inputs, folder=None, workers=None):
    """Run an lbt-recipes recipe and return its project folder.

    Recipes shell out to Radiance, so this can take minutes; it is deliberately
    the only place that blocks."""
    if not RECIPES_AVAILABLE:
        print("[PyOps.honeybee] lbt-recipes não está instalado.")
        return None
    if radiance_path() is None:
        print(
            "[PyOps.honeybee] Radiance não encontrado no PATH — as receitas "
            "precisam dos executáveis, não só dos pacotes Python."
        )
        return None

    try:
        recipe = recipes.Recipe(name)
        for key, value in inputs.items():
            if value is not None:
                recipe.input_value_by_name(key, value)
        settings = None
        if workers:
            RecipeSettings = optional("lbt_recipes.settings", "RecipeSettings")
            if RecipeSettings is not None:
                settings = RecipeSettings(workers=workers, folder=folder)
        elif folder:
            RecipeSettings = optional("lbt_recipes.settings", "RecipeSettings")
            if RecipeSettings is not None:
                settings = RecipeSettings(folder=folder)
        return recipe.run(settings, radiance_check=True)
    except ModuleNotFoundError as e:
        # A recipe describes its inputs with handler functions that live in
        # separate packages. lbt-recipes imports them while merely READING the
        # recipe, so a missing handler fails before any simulation starts — and
        # the traceback points at recipe.py, not at the missing install.
        print(
            f"[PyOps.honeybee] Falta o pacote '{e.name}'. Instale no console:\n"
            "    import subprocess, sys\n"
            "    subprocess.run([sys.executable, '-m', 'pip', 'install',\n"
            "        'pollination-handlers', 'honeybee-display',\n"
            "        'honeybee-radiance-postprocess',\n"
            "        '--target', r'<pasta site-packages do Bonsai>'])"
        )
        return None
    except Exception as e:
        print(f"[PyOps.honeybee] receita '{name}' falhou: {e}")
        traceback.print_exc()
        return None


def recipe_output(name, project_folder, output_name):
    if not RECIPES_AVAILABLE or project_folder is None:
        return None
    try:
        recipe = recipes.Recipe(name)
        return recipe.output_value_by_name(output_name, project_folder)
    except Exception as e:
        print(f"[PyOps.honeybee] saída '{output_name}': {e}")
        return None


def run_succeeded(project_folder):
    """Whether the recipe actually produced results.

    Recipes return their project folder whether or not the simulation worked,
    and the failure then surfaces as an unreadable result folder — which reads
    like a path problem and is not one. A logged ERROR is the honest signal."""
    if not project_folder:
        return False
    log_folder = os.path.join(project_folder, "__logs__")
    for name in ("err.log", "logs.log"):
        path = os.path.join(log_folder, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        except Exception:
            continue
        if "ERROR" in content or "failed" in content.lower():
            return False
    return True


def describe_model(model, sensor_count=0):
    """Log what was actually handed to Radiance.

    Context that silently failed to convert looks identical to context that was
    never selected, and the only way to tell them apart is to count what went
    into the model."""
    try:
        faces = len(model.faces)
        apertures = len(model.apertures)
        doors = len(model.doors)
        shades = len(model.shades)
    except Exception as e:
        print(f"[PyOps.honeybee] modelo ilegível: {e}")
        return
    print(
        f"[PyOps.honeybee] Modelo: {faces} faces, {apertures} aberturas, "
        f"{doors} portas, {shades} sombras (contexto), {sensor_count} sensores."
    )
    if not shades:
        print("[PyOps.honeybee] Sem geometria de contexto no modelo.")
    if not apertures:
        print(
            "[PyOps.honeybee] Sem aberturas: um modelo fechado não recebe luz "
            "e todo resultado sai zero."
        )


def find_result_files(project_folder, *relative_paths):
    """Result files for one sensor grid, found by walking the project folder.

    The recipe's own output handlers validate the folder shape and refuse it
    when the layout differs by a level — "Invalid result folder" — even though
    the numbers are sitting right there. Reading the files directly sidesteps
    the handler entirely, and the format is stable: one value per line, one file
    per grid.

    Several candidate paths are accepted because the same metric lives under
    different names across recipe versions."""
    if not project_folder:
        return []
    for relative in relative_paths:
        folder = os.path.join(project_folder, *relative.split("/"))
        if not os.path.isdir(folder):
            continue
        files = [
            os.path.join(folder, name)
            for name in sorted(os.listdir(folder))
            if os.path.isfile(os.path.join(folder, name))
            and not name.startswith(".")
            and not name.endswith((".json", ".txt", ".log"))
        ]
        if files:
            return files
    return []


def read_grid_results(project_folder, *relative_paths):
    """The numbers for the single grid this operator submits."""
    files = find_result_files(project_folder, *relative_paths)
    if not files:
        return None
    values = []
    for path in files:
        part = read_result_file(path)
        if part:
            values.extend(part)
    return values or None


def read_result_file(path):
    """One number per line — the shape every Radiance post-process writes."""
    try:
        with open(path, encoding="utf-8") as handle:
            return [
                float(token)
                for line in handle
                for token in line.split()
                if token
            ]
    except Exception as e:
        print(f"[PyOps.honeybee] resultado '{path}': {e}")
        return None


def flatten_results(result):
    """Recipe outputs arrive as a path, a list of paths, or already as numbers.

    Normalised here so the operator writes attributes the same way whatever the
    recipe handed back."""
    if result is None:
        return None
    if isinstance(result, (int, float)):
        return [float(result)]
    if isinstance(result, str):
        if os.path.isfile(result):
            return read_result_file(result)
        return None
    if isinstance(result, (list, tuple)):
        values = []
        for item in result:
            part = flatten_results(item)
            if part:
                values.extend(part)
            elif isinstance(item, (int, float)):
                values.append(float(item))
        return values or None
    return None


# --- WRITE-BACK ---


def write_face_values(obj, name, values, indices=None, domain="FACE"):
    """Numbers onto a Blender attribute, leaving untouched elements alone."""
    mesh = getattr(obj, "data", None)
    if not mesh:
        return False
    attribute = mesh.attributes.get(name)
    if attribute is not None and (
        attribute.data_type != "FLOAT" or attribute.domain != domain
    ):
        mesh.attributes.remove(attribute)
        attribute = None

    count = len(mesh.vertices) if domain == "POINT" else len(mesh.polygons)
    if attribute is None:
        attribute = mesh.attributes.new(name=name, type="FLOAT", domain=domain)
        current = [0.0] * count
    else:
        current = [0.0] * count
        attribute.data.foreach_get("value", current)

    targets = indices if indices is not None else range(count)
    for position, index in enumerate(targets):
        if index < count and position < len(values):
            current[index] = float(values[position])
    attribute.data.foreach_set("value", current)
    mesh.update()
    return True


# --- ENERGYPLUS ---
#
# No OpenStudio: the model goes through model_to_idf + run_idf, the path
# tools/energyplus_smoke.py verified. That is why HVAC is always an
# IdealAirSystem — detailed systems need the OpenStudio translation.

ENERGY_AVAILABLE = False
ENERGY_IMPORT_ERROR = None

# Thermal fallbacks for a layer whose material states nothing: a generic
# masonry, dense enough not to make a wall vanish thermally. Always reported.
DEFAULT_CONDUCTIVITY = 0.9
DEFAULT_DENSITY = 1920.0
DEFAULT_SPECIFIC_HEAT = 790.0

# Window/door fallbacks when the Openings pset lacks U_Factor/SHGC: single clear
# glass and a solid timber door. Always reported.
DEFAULT_GLAZING_U = 5.8
DEFAULT_GLAZING_SHGC = 0.8
DEFAULT_DOOR_U = 2.5

# A room face and a construction region belong together when parallel and no
# farther apart than the element's thickness plus this.
ROOM_MATCH_SLACK = 0.02

# A single layer this thick is almost always millimetres read as metres.
SUSPICIOUS_LAYER = 2.0

ENERGY_OUTPUTS = {
    "air_temp": "Zone Mean Air Temperature",
    "operative_temp": "Zone Operative Temperature",
    "cooling": "Zone Ideal Loads Supply Air Total Cooling Energy",
    "heating": "Zone Ideal Loads Supply Air Total Heating Energy",
    "lights": "Zone Lights Electricity Energy",
    "equipment": "Zone Electric Equipment Electricity Energy",
    "occupants": "Zone People Occupant Count",
}


def ensure_energy():
    """honeybee-energy on its own — the EnergyPlus operator must not depend on
    honeybee-radiance or lbt-recipes being importable."""
    global ENERGY_AVAILABLE, ENERGY_IMPORT_ERROR
    if ENERGY_AVAILABLE:
        return True
    try:
        import honeybee.model
        import honeybee.room
        import honeybee_energy.writer
        import honeybee_energy.run
        import honeybee_energy.simulation.parameter
    except Exception:
        ENERGY_IMPORT_ERROR = traceback.format_exc()
        return False
    ENERGY_AVAILABLE = True
    ENERGY_IMPORT_ERROR = None
    return True


def energyplus_path():
    """EnergyPlus installation folder as honeybee-energy found it, or None."""
    if not ensure_energy():
        return None
    from honeybee_energy.config import folders

    return folders.energyplus_path or None


def energyplus_check():
    """Whether the IDF honeybee-energy writes will name the executable's version.

    energyplus_idf_version copies the version sensed from the executable, so a
    mismatch here means the executable could not be queried — and EnergyPlus
    refuses an IDF without a Version it recognises."""
    if energyplus_path() is None:
        print(
            "[PyOps.energyplus] EnergyPlus não encontrado. O honeybee-energy "
            "procura C:\\EnergyPlusV*, o EnergyPlus do OpenStudio ou "
            "energyplus_path no config.json do honeybee_energy."
        )
        return False
    import re

    from honeybee_energy.config import folders
    from honeybee_energy.writer import energyplus_idf_version

    exe = folders.energyplus_version
    header = energyplus_idf_version() or ""
    match = re.search(r"Version,\s*([\d.]+)", header)
    if exe is None or match is None:
        print(
            f"[PyOps.energyplus] Versão do EnergyPlus ilegível em "
            f"{folders.energyplus_exe} — o executável não respondeu a --version."
        )
        return False
    if match.group(1) != ".".join(str(d) for d in exe):
        print(
            f"[PyOps.energyplus] IDF escreveria Version {match.group(1)}, "
            f"executável é {folders.energyplus_version_str}."
        )
        return False
    return True


def is_external(element):
    """IsExternal from the common pset of whichever class the element is, or None.

    All three names are tried rather than chosen by class: a slab used as a
    roof keeps Pset_SlabCommon, and guessing the class wrong would read as
    'no pset' and silently default."""
    import pyops_api

    bim = pyops_api.bim_helpers
    if element is None:
        return None
    for pset in ("Pset_WallCommon", "Pset_SlabCommon", "Pset_RoofCommon"):
        value = bim.pset_value(element, f"{pset}.IsExternal")
        if value is not None:
            return bool(value)
    return None


def energy_room(obj, identifier="pyops_room", tolerance=0.01):
    """(Room, faces) from a closed Room Reference mesh, or (None, []).

    One honeybee Face per planar region, outer outline only: a hole in the
    Room Reference is a window position, not a gap in the volume. The face
    geometry comes from Polyface3D.from_faces, which turns every face outward —
    and the Face is created from that, so its type (wall/floor/roof) is judged
    on the outward normal rather than on however the mesh happened to wind.

    Each entry of faces: {"face", "normal", "outline", "centre", "holes"},
    vectors in world space, normal pointing out of the room."""
    from honeybee.face import Face
    from honeybee.room import Room
    from ladybug_geometry.geometry3d.polyface import Polyface3D

    regions = planar_regions(obj)
    face3ds, kept = [], []
    for region in regions:
        face3d = _face3d(region["outer"])
        if face3d is not None:
            face3ds.append(face3d)
            kept.append(region)
    if len(face3ds) < 4:
        print(f"[PyOps.energyplus] '{obj.name}' tem {len(face3ds)} regiões planas — não é um volume.")
        return None, []

    polyface = Polyface3D.from_faces(face3ds, tolerance)
    if not polyface.is_solid:
        polyface = polyface.merge_overlapping_edges(tolerance)
    if not polyface.is_solid:
        print(
            f"[PyOps.energyplus] Room Reference '{obj.name}' não é sólido fechado "
            f"({len(polyface.naked_edges)} arestas nuas, "
            f"{len(polyface.non_manifold_edges)} não-manifold)."
        )
        return None, []
    if polyface.volume <= 0:
        print(f"[PyOps.energyplus] Room Reference '{obj.name}' tem volume ≤ 0.")
        return None, []

    faces, entries = [], []
    for index, (region, outward) in enumerate(zip(kept, polyface.faces)):
        normal = mathutils.Vector((outward.normal.x, outward.normal.y, outward.normal.z))
        outline = region["outer"]
        if normal.dot(region["normal"]) < 0:
            outline = list(reversed(outline))
        try:
            face = Face(f"{identifier}_f{index}", outward)
        except Exception as e:
            print(f"[PyOps.energyplus] face {index} do Room: {e}")
            return None, []
        faces.append(face)
        entries.append(
            {
                "face": face,
                "normal": normal,
                "outline": outline,
                "centre": _loop_centroid(outline),
                "holes": region["holes"],
            }
        )
    try:
        room = Room(identifier, faces, tolerance=tolerance, angle_tolerance=1.0)
    except Exception as e:
        print(f"[PyOps.energyplus] Room: {e}")
        return None, []
    return room, entries


def element_thickness(element):
    import pyops_api

    return sum(t for t, _m in pyops_api.bim_helpers.material_layers(element))


def match_room_faces(entries, objects):
    """Tag each room face with the construction element it stands for.

    Parallel planar region, no farther than the element's thickness plus
    ROOM_MATCH_SLACK, and some sample of the room face inside the region's
    outline; nearest wins. The test runs that way round because one wall
    element often serves several rooms — its region is larger than any room face.

    Samples are the centre plus the midpoints from the centre to each outline
    vertex, not the centre alone: a door down to the floor is a notch in the
    wall's OUTER outline, and a door centred on the wall swallows the centre.

    A second element that also passes, or whose region centre falls inside the
    same room face, means the face covers two elements; only the nearest
    counts, and it is reported."""
    import pyops_api

    bim = pyops_api.bim_helpers
    candidates = []
    for obj in objects:
        element = bim.entity(obj)
        thickness = element_thickness(element) if element is not None else 0.0
        candidates.append((obj, element, thickness, planar_regions(obj)))

    for entry in entries:
        normal, centre = entry["normal"], entry["centre"]
        samples = [centre] + [(centre + p) / 2.0 for p in entry["outline"]]
        best = None
        others = set()
        for obj, element, thickness, regions in candidates:
            for region in regions:
                if abs(region["normal"].dot(normal)) < PLANE_COS:
                    continue
                distance = abs((region["outer"][0] - centre).dot(normal))
                if distance > thickness + ROOM_MATCH_SLACK:
                    continue
                if any(_inside_loop(s, region["outer"], normal, PLANE_DISTANCE) for s in samples):
                    others.add(obj.name)
                    if best is None or distance < best[0]:
                        best = (distance, obj, element, thickness)
                elif _inside_loop(_loop_centroid(region["outer"]), entry["outline"],
                                  normal, PLANE_DISTANCE):
                    others.add(obj.name)
        entry["obj"] = best[1] if best else None
        entry["element"] = best[2] if best else None
        entry["thickness"] = best[3] if best else 0.0
        others.discard(entry["obj"].name if entry["obj"] else None)
        if best and others:
            print(
                f"[PyOps.energyplus] face {entry['face'].identifier} cobre mais de um "
                f"elemento ({best[1].name}, {', '.join(sorted(others))}) — "
                f"usando só {best[1].name}."
            )
    return entries


def _layer_property(properties, name, fallback, missing):
    try:
        return float(properties[name])
    except (KeyError, TypeError, ValueError):
        missing.append(f"{name}={fallback:g}")
        return fallback


def thermal_construction(element, first_outer=True, cache=None):
    """OpaqueConstruction from the element's IfcMaterialLayerSet, or None.

    honeybee lists materials outside to inside. first_outer says layer 1 of the
    set is the exterior one; the other way round reverses the list. Constructions
    are cached by layer signature, so ten walls of one type build one construction.

    Properties are read by name from any pset of the material: MassDensity lives
    in Pset_MaterialCommon in IFC4, the thermal ones in Pset_MaterialThermal."""
    import pyops_api

    from honeybee.typing import clean_ep_string
    from honeybee_energy.construction.opaque import OpaqueConstruction
    from honeybee_energy.material.opaque import EnergyMaterial

    bim = pyops_api.bim_helpers
    layers = bim.material_layers(element)
    if not layers:
        return None
    cache = {} if cache is None else cache
    signature = (
        tuple((round(t, 5), m.Name if m else None) for t, m in layers),
        first_outer,
    )
    if signature in cache:
        return cache[signature]

    materials = []
    for thickness, material in layers:
        name = material.Name if material is not None else "unnamed"
        if thickness <= 0:
            print(f"[PyOps.energyplus] camada '{name}' com espessura 0 ignorada.")
            continue
        if thickness > SUSPICIOUS_LAYER:
            print(
                f"[PyOps.energyplus] camada '{name}' com {thickness:.2f} m — "
                "unidade do IFC errada?"
            )
        properties = bim.material_properties(material)
        missing = []
        conductivity = _layer_property(properties, "ThermalConductivity", DEFAULT_CONDUCTIVITY, missing)
        density = _layer_property(properties, "MassDensity", DEFAULT_DENSITY, missing)
        specific_heat = _layer_property(properties, "SpecificHeatCapacity", DEFAULT_SPECIFIC_HEAT, missing)
        if missing:
            print(f"[PyOps.energyplus] material '{name}' sem valor, usando padrão: {', '.join(missing)}.")
        try:
            materials.append(
                EnergyMaterial(
                    clean_ep_string(f"{name}_{thickness * 1000:.1f}mm"),
                    thickness, conductivity, density, specific_heat,
                )
            )
        except Exception as e:
            print(f"[PyOps.energyplus] material '{name}': {e}")
            return None
    if not materials:
        return None
    if not first_outer:
        materials.reverse()
    try:
        construction = OpaqueConstruction(
            # Named by position, not by element: the cache shares it across every
            # element with the same layers.
            f"pyops_layers_{len(cache)}",
            materials,
        )
    except Exception as e:
        print(f"[PyOps.energyplus] construção de {element.is_a()} {element.id()}: {e}")
        return None
    cache[signature] = construction
    return construction


def apply_envelope(entries, first_outer=True):
    """Construction and boundary condition on every room face.

    IsExternal False → Adiabatic. True, or no pset at all → the exterior of the
    face's type: Ground for a floor, Outdoors otherwise. A face with no element
    keeps honeybee's generic construction and default boundary."""
    from honeybee.boundarycondition import boundary_conditions
    from honeybee.facetype import Floor

    cache = {}
    for entry in entries:
        face, element = entry["face"], entry["element"]
        if element is None:
            print(
                f"[PyOps.energyplus] face {face.identifier} ({face.type}) sem elemento "
                "de Construction casado — construção genérica."
            )
            continue
        construction = thermal_construction(element, first_outer, cache)
        if construction is None:
            print(
                f"[PyOps.energyplus] {element.is_a()} '{entry['obj'].name}' sem "
                "IfcMaterialLayerSet utilizável — construção genérica."
            )
        else:
            face.properties.energy.construction = construction

        external = is_external(element)
        if external is None:
            print(
                f"[PyOps.energyplus] '{entry['obj'].name}' sem IsExternal em "
                "Pset_Wall/Slab/RoofCommon — tratado como externo."
            )
        if external is False:
            face.boundary_condition = boundary_conditions.adiabatic
        elif isinstance(face.type, Floor):
            face.boundary_condition = boundary_conditions.ground
        else:
            face.boundary_condition = boundary_conditions.outdoors
    return entries


def _subface_values(obj, pset_name):
    """(vt, u, shgc, notes) with the fallbacks applied and named."""
    values = glazing_values(obj, pset_name) if obj is not None else {}
    notes = []

    def number(key, fallback, low, high):
        try:
            return max(low, min(high, float(values.get(key))))
        except (TypeError, ValueError):
            notes.append(f"{key} padrão {fallback}")
            return fallback

    vt = number("vt", DEFAULT_GLAZING_VT, 0.0, 1.0)
    opaque = vt <= 0.01
    u = number("u_factor", DEFAULT_DOOR_U if opaque else DEFAULT_GLAZING_U, 0.01, 12.0)
    shgc = 0.0 if opaque else number("shgc", DEFAULT_GLAZING_SHGC, 0.0, 1.0)
    return vt, u, shgc, notes


def energy_subface(name, face3d, obj, pset_name):
    """Aperture (VT > 0.01) with a SimpleGlazSys, or Door with a no-mass layer.

    An opaque filling has no glazing to describe, so its U becomes an R-value
    layer. U-factors include air films and R = 1/U counts them twice — a small,
    conservative error, accepted."""
    from honeybee.aperture import Aperture
    from honeybee.door import Door
    from honeybee.typing import clean_ep_string
    from honeybee_energy.construction.opaque import OpaqueConstruction
    from honeybee_energy.construction.window import WindowConstruction
    from honeybee_energy.material.glazing import EnergyWindowMaterialSimpleGlazSys
    from honeybee_energy.material.opaque import EnergyMaterialNoMass

    vt, u, shgc, notes = _subface_values(obj, pset_name)
    label = obj.name if obj is not None else name
    if notes and obj is not None:
        print(f"[PyOps.energyplus] abertura '{label}': {', '.join(notes)}.")
    ident = clean_ep_string(name)
    try:
        if vt > 0.01:
            sub = Aperture(ident, face3d)
            if obj is not None:
                material = EnergyWindowMaterialSimpleGlazSys(f"{ident}_glz", u, shgc, vt)
                sub.properties.energy.construction = WindowConstruction(f"{ident}_constr", [material])
        else:
            sub = Door(ident, face3d)
            material = EnergyMaterialNoMass(f"{ident}_mat", 1.0 / u)
            sub.properties.energy.construction = OpaqueConstruction(f"{ident}_constr", [material])
        return sub
    except Exception as e:
        print(f"[PyOps.energyplus] abertura '{label}': {e}")
        return None


def _project(points, entry):
    normal, centre = entry["normal"], entry["centre"]
    return [p - normal * (p - centre).dot(normal) for p in points]


def _add_subface(entry, points, obj, pset_name, name):
    """Put a sub-face on a room face, oriented with it. False when refused."""
    face = entry["face"]
    outside = [p for p in points if not _inside_loop(p, entry["outline"], entry["normal"], PLANE_DISTANCE)]
    if outside:
        print(
            f"[PyOps.energyplus] abertura '{obj.name if obj else name}' sai da face "
            f"{face.identifier} — descartada (o EnergyPlus abortaria)."
        )
        return False
    if _loop_normal(points).dot(entry["normal"]) < 0:
        points = list(reversed(points))
    face3d = _face3d(points)
    if face3d is None:
        return False
    sub = energy_subface(name, face3d, obj, pset_name)
    if sub is None:
        return False
    from honeybee.aperture import Aperture
    from honeybee.boundarycondition import Outdoors

    if not isinstance(face.boundary_condition, Outdoors):
        print(
            f"[PyOps.energyplus] abertura '{obj.name if obj else name}' em face "
            f"{face.boundary_condition} — ignorada (só faces Outdoors levam aberturas)."
        )
        return False
    try:
        if isinstance(sub, Aperture):
            face.add_aperture(sub)
        else:
            face.add_door(sub)
    except Exception as e:
        print(f"[PyOps.energyplus] abertura não anexada: {e}")
        return False
    return True


def _loop_normal(points):
    total = mathutils.Vector((0.0, 0.0, 0.0))
    for index, point in enumerate(points):
        total += point.cross(points[(index + 1) % len(points)])
    return total.normalized() if total.length > 0 else total


def openings_cut(entries, opening_objects, pset_name):
    """Windows from holes cut in the Room Reference itself.

    The Room Reference has no depth, so each hole is padded by the thickness of
    the element its face stands for — that is how deep into the wall the
    filling object can sit and still belong to the hole."""
    voids, owners = [], []
    for entry in entries:
        for points in entry["holes"]:
            voids.append(
                {
                    "normal": entry["normal"],
                    "pad": max(0.05, entry["thickness"] + ROOM_MATCH_SLACK),
                    "loops": [
                        {
                            "points": points,
                            "centre": _loop_centroid(points),
                            "area": _loop_area(points, entry["normal"]),
                        }
                    ],
                }
            )
            owners.append(entry)
    matches = match_openings(voids, opening_objects)
    added = 0
    for index, (void, entry) in enumerate(zip(voids, owners)):
        obj = matches.get(index)
        if obj is None:
            c = void["loops"][0]["centre"]
            print(
                f"[PyOps.energyplus] furo em ({c.x:.2f}, {c.y:.2f}, {c.z:.2f}) sem objeto "
                "de Openings — janela genérica."
            )
        points = void["loops"][0]["points"]
        if _add_subface(entry, points, obj, pset_name, f"pyops_cut_{index}"):
            added += 1
    return added


def _nearest_entry(entries, normal, point, reach, host=None):
    """Room face parallel to normal, containing point's projection, within reach.
    A face standing for the host element wins over a nearer one that doesn't."""
    best = None
    for entry in entries:
        if abs(entry["normal"].dot(normal)) < PLANE_COS:
            continue
        distance = abs((point - entry["centre"]).dot(entry["normal"]))
        if distance > reach(entry):
            continue
        if not _inside_loop(point, entry["outline"], entry["normal"], PLANE_DISTANCE):
            continue
        key = (0 if host is not None and entry["obj"] is host else 1, distance)
        if best is None or key < best[0]:
            best = (key, entry)
    return best[1] if best else None


def openings_projected(entries, construction_objects, opening_objects, pset_name):
    """Windows from the voids Bonsai cut in the real elements, projected onto
    the room face in front of them — the Radiance pipeline, then a projection."""
    voids = []
    for obj in construction_objects:
        for void in pair_voids(planar_regions(obj)):
            void["host"] = obj
            voids.append(void)
    matches = match_openings(voids, opening_objects)

    added = 0
    for index, void in enumerate(voids):
        obj = matches.get(index)
        host = void["host"]
        loop = min(
            void["loops"],
            key=lambda l: min(
                (abs((l["centre"] - e["centre"]).dot(e["normal"])) for e in entries
                 if abs(e["normal"].dot(void["normal"])) >= PLANE_COS),
                default=0.0,
            ),
        )
        entry = _nearest_entry(
            entries, void["normal"], loop["centre"],
            lambda e: e["thickness"] + ROOM_MATCH_SLACK, host,
        )
        if entry is None:
            continue  # a void in a wall of some other room
        if obj is None:
            c = loop["centre"]
            print(
                f"[PyOps.energyplus] vão em '{host.name}' ({c.x:.2f}, {c.y:.2f}, "
                f"{c.z:.2f}) sem objeto de Openings — janela genérica."
            )
        points = _project(loop["points"], entry)
        if _add_subface(entry, points, obj, pset_name, f"pyops_void_{index}"):
            added += 1

    # A filling whose void touches the element edge cuts a notch, not a loop.
    matched = {obj.name for obj in matches.values()}
    for position, obj in enumerate(opening_objects):
        if obj.name in matched:
            continue
        outline = largest_outline(obj)
        if outline is None:
            continue
        normal, points = outline
        centre = _loop_centroid(points)
        entry = _nearest_entry(
            entries, normal, centre,
            lambda e: e["thickness"] + ROOM_MATCH_SLACK + 0.05,
        )
        if entry is None:
            continue
        print(
            f"[PyOps.energyplus] '{obj.name}' não casou com laço interno — "
            "projetando a maior face do objeto."
        )
        if _add_subface(entry, _project(points, entry), obj, pset_name,
                        f"pyops_opening_{position}"):
            added += 1
    return added


def program_type(identifier):
    """ProgramType from the honeybee-energy library, or None with the five
    closest names printed — a typo should cost one look, not a trip to the docs."""
    import difflib

    from honeybee_energy.lib.programtypes import PROGRAM_TYPES, program_type_by_identifier

    try:
        return program_type_by_identifier(identifier)
    except Exception:
        close = difflib.get_close_matches(identifier or "", PROGRAM_TYPES, n=5, cutoff=0.0)
        print(
            f"[PyOps.energyplus] Program '{identifier}' não existe. Mais próximos:\n    "
            + "\n    ".join(close)
        )
        return None


def lighting_watts(objects, pset_name):
    """Σ Wattage of the fixtures, LuminousFlux / DEFAULT_EFFICACY where only flux is known."""
    total = 0.0
    for obj, flux, watts in light_readings(objects, pset_name):
        try:
            total += float(watts)
            continue
        except (TypeError, ValueError):
            pass
        try:
            total += float(flux) / DEFAULT_EFFICACY
            print(
                f"[PyOps.energyplus] luz '{obj.name}' só com LuminousFlux — "
                f"{float(flux) / DEFAULT_EFFICACY:.1f} W a {DEFAULT_EFFICACY:g} lm/W."
            )
        except (TypeError, ValueError):
            print(f"[PyOps.energyplus] luz '{obj.name}' sem Wattage nem LuminousFlux — ignorada.")
    return total


def hvac_readings(objects, pset_name):
    """(cooling W, heating W, COP cooling, COP heating) over the objects.

    Limits add up; each COP is the capacity-weighted harmonic mean,
    Σ Cap / Σ (Cap / COP) — what the units would draw together at full load.
    A unit without a COP still counts towards the limit, not towards the COP.
    None where nothing could be aggregated."""
    import pyops_api

    bim = pyops_api.bim_helpers
    totals = {"cool": [0.0, 0.0, 0.0], "heat": [0.0, 0.0, 0.0]}  # limit, Σcap w/ COP, Σcap/COP
    for obj in objects:
        element = bim.entity(obj)
        if element is None:
            print(f"[PyOps.energyplus] HVAC '{obj.name}' não é elemento IFC — ignorado.")
            continue
        for key, cap_name, cop_name in (
            ("cool", "CoolingCapacity", "COP_Cooling"),
            ("heat", "HeatingCapacity", "COP_Heating"),
        ):
            try:
                capacity = float(bim.pset_value(element, f"{pset_name}.{cap_name}"))
            except (TypeError, ValueError):
                continue
            totals[key][0] += capacity
            try:
                cop = float(bim.pset_value(element, f"{pset_name}.{cop_name}"))
                if cop <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                if capacity > 0:
                    print(f"[PyOps.energyplus] HVAC '{obj.name}' sem {cop_name} — fora do COP agregado.")
                continue
            totals[key][1] += capacity
            totals[key][2] += capacity / cop

    def cop(key):
        return totals[key][1] / totals[key][2] if totals[key][2] > 0 else None

    return totals["cool"][0], totals["heat"][0], cop("cool"), cop("heat")


def constant_schedule(identifier, value, limit="Temperature"):
    from honeybee_energy.lib.scheduletypelimits import schedule_type_limit_by_identifier
    from honeybee_energy.schedule.ruleset import ScheduleRuleset

    return ScheduleRuleset.from_constant_value(
        identifier, value, schedule_type_limit_by_identifier(limit)
    )


def simulate_energy(model, epw_path, folder, month=None):
    """Run the model through EnergyPlus; the .sql path, or None with the reason printed.

    The IDF is Version + SimulationParameter + model, the order honeybee's own
    CLI writes: model_to_idf alone emits neither header. Zone sizing is on by
    default and needs design days, so they come from the DDY beside the EPW or
    are approximated from the EPW itself. month=None runs the whole year."""
    import calendar

    from honeybee_energy.run import run_idf
    from honeybee_energy.simulation.parameter import SimulationParameter
    from honeybee_energy.simulation.runperiod import RunPeriod
    from honeybee_energy.writer import energyplus_idf_version, model_to_idf
    from ladybug.analysisperiod import AnalysisPeriod

    sim_par = SimulationParameter()
    for name in ENERGY_OUTPUTS.values():
        sim_par.output.add_output(name)
    sim_par.output.reporting_frequency = "Hourly"
    if month:
        sim_par.run_period = RunPeriod.from_analysis_period(
            AnalysisPeriod(st_month=month, end_month=month,
                           end_day=calendar.monthrange(2017, month)[1])
        )
    ddy = os.path.splitext(epw_path)[0] + ".ddy"
    try:
        sim_par.sizing_parameter.add_from_ddy_996_004(ddy)
    except Exception:
        try:
            from ladybug.epw import EPW

            epw = EPW(epw_path)
            sim_par.sizing_parameter.design_days = [
                epw.approximate_design_day("WinterDesignDay"),
                epw.approximate_design_day("SummerDesignDay"),
            ]
        except Exception as e:
            print(f"[PyOps.energyplus] design days: {e}")
            return None

    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    try:
        idf_str = "\n\n".join(
            [energyplus_idf_version(), sim_par.to_idf(), model_to_idf(model)]
        )
    except Exception as e:
        print(f"[PyOps.energyplus] IDF não gerado: {e}")
        traceback.print_exc()
        return None
    idf = os.path.join(folder, "in.idf")
    with open(idf, "w") as handle:
        handle.write(idf_str)
    # Copied so run_idf's .stat renaming never touches the user's weather folder.
    epw_copy = shutil.copy(epw_path, folder)

    try:
        sql, _zsz, _rdd, _html, err = run_idf(idf, epw_copy, silent=True)
    except Exception as e:
        print(f"[PyOps.energyplus] run_idf: {e}")
        return None

    text = ""
    if err and os.path.isfile(err):
        with open(err, errors="replace") as handle:
            text = handle.read()
    if sql is None or not os.path.isfile(sql) or "** Fatal" in text or "**  Fatal" in text:
        print(f"[PyOps.energyplus] EnergyPlus falhou em {folder}.")
        for line in text.splitlines():
            if "Severe" in line or "Fatal" in line or line.strip().startswith("**   ~~~"):
                print("    " + line.strip())
        return None
    severe = text.count("** Severe")
    warnings = text.count("** Warning")
    if severe:
        print(f"[PyOps.energyplus] {severe} erro(s) Severe não fatais — ver {err}")
    print(f"[PyOps.energyplus] simulação ok ({warnings} warnings): {folder}")
    return sql


def energy_series(sql_path):
    """{key of ENERGY_OUTPUTS: hourly collection or None} for the single zone."""
    from ladybug.sql import SQLiteResult

    result = SQLiteResult(sql_path)
    series = {}
    for key, name in ENERGY_OUTPUTS.items():
        try:
            collections = result.data_collections_by_output_name(name)
        except Exception as e:
            print(f"[PyOps.energyplus] '{name}': {e}")
            collections = []
        series[key] = collections[0] if collections else None
    return series


def comfortable_flags(series, epw_path, mode, low=None, high=None):
    """Per-hour 1/0 on operative temperature: ASHRAE-55 adaptive (80 %) or a fixed band.

    The adaptive model reads the prevailing outdoor temperature off the whole
    EPW, so a single-month run still gets the right monthly mean."""
    operative = series.get("operative_temp")
    if operative is None:
        print("[PyOps.energyplus] sem Zone Operative Temperature — conforto não calculado.")
        return None
    if mode == "FIXED":
        return [1 if low <= t <= high else 0 for t in operative.values]
    try:
        from ladybug.epw import EPW
        from ladybug_comfort.collection.adaptive import Adaptive

        return list(Adaptive(EPW(epw_path).dry_bulb_temperature, operative).is_comfortable)
    except Exception as e:
        print(f"[PyOps.energyplus] conforto adaptativo: {e}")
        return None


def summarise_energy(series, flags, months=None, occupied_only=False, cops=None,
                     hvac_electricity=False):
    """{month or None: {metric: value}} aggregated from the hourly series.

    months=None → one bucket for the whole run. Electricity is lights +
    equipment; with hvac_electricity the ideal loads are divided by the
    aggregated COPs on top — outside the simulation, hence approximate."""
    reference = series.get("air_temp") or series.get("operative_temp")
    if reference is None:
        return {}
    datetimes = reference.datetimes
    occupants = series.get("occupants")
    if occupied_only and occupants is None:
        print("[PyOps.energyplus] Program sem ocupação — conforto sobre todas as horas.")
        occupied_only = False

    def values(key):
        collection = series.get(key)
        return collection.values if collection is not None else [0.0] * len(datetimes)

    air, cooling, heating = values("air_temp"), values("cooling"), values("heating")
    lights, equipment = values("lights"), values("equipment")
    occupied = occupants.values if occupants is not None else None
    cop_cool, cop_heat = cops or (None, None)

    buckets = {}
    for month in (months or [None]):
        hours = [i for i, dt in enumerate(datetimes) if month is None or dt.month == month]
        if not hours:
            continue
        considered = [i for i in hours if not occupied_only or occupied[i] > 0]
        summary = {"air_temp_mean": sum(air[i] for i in hours) / len(hours)}
        if flags is not None:
            summary["comfortable_hours_pct"] = (
                100.0 * sum(flags[i] for i in considered) / len(considered)
                if considered else 0.0
            )
        summary["cooling_load_kwh"] = sum(cooling[i] for i in hours)
        summary["heating_load_kwh"] = sum(heating[i] for i in hours)
        electricity = sum(lights[i] + equipment[i] for i in hours)
        if hvac_electricity:
            if cop_cool:
                electricity += summary["cooling_load_kwh"] / cop_cool
            if cop_heat:
                electricity += summary["heating_load_kwh"] / cop_heat
        summary["electricity_kwh"] = electricity
        buckets[month] = summary
    return buckets
