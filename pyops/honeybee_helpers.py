"""Blender → Honeybee → Radiance bridge.

Lives in the addon so the planned artificial-lighting and IES work can share it:

    import refops_api
    hb = refops_api.honeybee_helpers

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
        print(f"[RefOps.honeybee] lbt-recipes indisponível: {e}")

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
    import refops_api; print(refops_api.honeybee_helpers.status())"""
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
        print(f"[RefOps.honeybee] '{module_path}' indisponível: {e}")
        return None
    if attribute is None:
        return module
    member = getattr(module, attribute, None)
    if member is None:
        print(f"[RefOps.honeybee] '{module_path}.{attribute}' não existe.")
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
            print(f"[RefOps.honeybee] Atributo '{name}' não existe.")
            continue
        if attribute.domain != "FACE" or attribute.data_type != "BOOLEAN":
            print(
                f"[RefOps.honeybee] '{name}' é {attribute.data_type}/"
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


def model_from_object(obj, attributes, context_collection=None, identifier="refops"):
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

    import refops_api

    lb = refops_api.ladybug_helpers

    classified = classify_faces(obj, attributes)
    if not classified:
        print("[RefOps.honeybee] Nenhuma face classificada pelos atributos.")
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
        print(f"[RefOps.honeybee] Model: {e}")
        return None


def model_from_ifc(storey=None, identifier="refops", context_collection=None,
                   options=None):
    """A Honeybee model built from the IFC, not from painted attributes.

    Every piece is already there and is more faithful than a proxy: IfcSpace is
    a closed volume, so it becomes a Room with real geometry instead of orphaned
    faces; an IfcWall is an opaque surface; and an IfcOpeningElement carries the
    true dimensions and position of the window that was cut, so the Aperture is
    the actual hole rather than a face someone drew.

    Rooms matter to Radiance: light entering a window belongs to a volume. A
    model of loose faces answers the question approximately; a model of rooms
    answers it.

    Returns None when there are no spaces, since the caller can still fall back
    to the attribute path."""
    import refops_api

    bim = refops_api.bim_helpers
    if not bim.ensure() or not AVAILABLE:
        return None
    ifc_file = bim.get_file()
    if ifc_file is None:
        return None

    from honeybee.aperture import Aperture
    from honeybee.face import Face
    from honeybee.model import Model
    from honeybee.room import Room
    from honeybee.shade import Shade

    lb = refops_api.ladybug_helpers

    try:
        spaces = ifc_file.by_type("IfcSpace")
    except Exception as e:
        print(f"[RefOps.honeybee] IfcSpace: {e}")
        return None
    if not spaces:
        print("[RefOps.honeybee] Nenhum IfcSpace — rode o operador de ambientes.")
        return None

    rooms = []
    for space in spaces:
        if storey is not None and not _in_storey(space, storey):
            continue
        obj = bim.object_for(space)
        if obj is None or obj.data is None or not obj.data.polygons:
            continue
        faces = []
        for face3d, index in lb.faces_from_object(obj):
            faces.append(Face(f"{obj.name}_f{index}", face3d))
        if not faces:
            continue
        # The space itself is air; its bounding surfaces take their optics from
        # the elements around them, applied further down.
        try:
            room = Room(obj.name, faces)
        except Exception as e:
            print(f"[RefOps.honeybee] '{obj.name}' não fechou um volume: {e}")
            continue
        rooms.append((room, obj))

    if not rooms:
        print("[RefOps.honeybee] Nenhum ambiente utilizável.")
        return None

    # Openings become apertures on whichever room face contains them. The void
    # is a solid through the wall, so its largest face is the one lying in the
    # wall plane — that is the outline to punch.
    # Surfaces take the optics of the host they belong to: a room face lying in
    # a wall gets that wall's modifier.
    if options and options.get("use_optics"):
        _apply_room_modifiers(rooms, _opening_hosts(ifc_file), options)

    for host in _opening_hosts(ifc_file):
        for relation in getattr(host, "HasOpenings", None) or []:
            opening = relation.RelatedOpeningElement
            obj = bim.object_for(opening)
            if obj is None or obj.data is None:
                continue
            outline = _largest_face(obj, lb)
            if outline is None:
                continue
            aperture = _punch(rooms, outline, Aperture, obj.name)
            if aperture is not None:
                _apply_modifier(aperture, _filling_of(opening) or opening, options)

    shades = []
    for position, face3d in enumerate(lb.collection_faces(context_collection)):
        shades.append(Shade(f"{identifier}_context_{position}", face3d))

    # Artificial light, as emitting surfaces.
    light_report = {}
    lights = _resolve_lights(options, rooms, ifc_file)
    if lights:
        emitters, light_report = light_shades(lights)
        shades.extend(emitters)
    if light_report and (options or {}).get("report"):
        print(f"[RefOps.honeybee] Luzes: {light_report}")

    try:
        model = Model(
            identifier,
            rooms=[room for room, _obj in rooms],
            orphaned_shades=shades,
            units="Meters",
        )
    except Exception as e:
        print(f"[RefOps.honeybee] Model: {e}")
        return None
    return model


def radiance_modifier(element, name, overrides=None, outermost=True, defaults=None):
    """A Radiance modifier built from the element's optical properties.

    Plastic for opaque surfaces, glass where light passes through: those are the
    two primitives Radiance actually uses, and which one applies is decided by
    the transmittance rather than by the IFC class — a glazed wall behaves like
    glass whatever it is called.

    Returns (modifier, sources) so the caller can report where each number came
    from; a value that quietly fell back to a default is the kind of thing that
    invalidates a study without anyone noticing."""
    import refops_api

    bim = refops_api.bim_helpers
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
        print(f"[RefOps.honeybee] modificador '{name}': {e}")
        return None, sources


# Custom property on an IfcLightFixture naming the Blender light that emits for
# it. IFC says where the luminaire is; the Blender light says how it emits, and
# this is what ties the two together without a proximity guess.
LIGHT_LINK_KEY = "refops_light"

# Luminous efficacy used when only wattage is known. 90 lm/W is a plain LED
# fixture — enough to be honest about, and the reason a measured lumen figure on
# the light is worth more than this.
DEFAULT_EFFICACY = 90.0


def blender_lights(collection=None):
    """Light objects to consider, from a collection or the whole scene."""
    if collection is not None:
        return [obj for obj in collection.all_objects if obj.type == "LIGHT"]
    return [obj for obj in bpy.context.scene.objects if obj.type == "LIGHT"]


def linked_lights(ifc_file=None):
    """Blender lights named by IfcLightFixture custom properties.

    Selecting fixtures geometrically is guesswork; naming the light on the
    fixture makes it explicit, and the distribute operator can stamp that
    property when it places them."""
    import refops_api

    bim = refops_api.bim_helpers
    if not bim.ensure():
        return []
    ifc_file = ifc_file or bim.get_file()
    if ifc_file is None:
        return []

    lights = []
    try:
        fixtures = ifc_file.by_type("IfcLightFixture")
    except Exception:
        return []
    for fixture in fixtures:
        obj = bim.object_for(fixture)
        if obj is None:
            continue
        name = obj.get(LIGHT_LINK_KEY)
        light = bpy.data.objects.get(name) if name else None
        if light is not None and light.type == "LIGHT":
            # The fixture's placement wins: the light describes emission, the
            # fixture describes where it hangs.
            lights.append((light, obj.matrix_world.copy()))
    return lights


def light_flux(light_obj):
    """Luminous flux in lumens.

    A `refops_lumens` custom property is used when present — a manufacturer
    figure beats anything derived. Otherwise the Blender wattage is converted
    with a stated efficacy, which is an assumption and is logged as one."""
    lumens = light_obj.get("refops_lumens")
    if lumens:
        try:
            return float(lumens), "refops_lumens"
        except (TypeError, ValueError):
            pass
    light = light_obj.data
    efficacy = float(light_obj.get("refops_efficacy") or DEFAULT_EFFICACY)
    return float(getattr(light, "energy", 0.0)) * efficacy, f"{efficacy:g} lm/W"


def light_geometry(light_obj, matrix=None, size=0.1):
    """A Face3D that stands in for the lamp.

    Radiance emits from surfaces, not from points, so a point or spot lamp
    becomes a small square facing its aim and an area lamp becomes its own
    rectangle. The square is deliberately small: its size changes the softness
    of the shadow, not the amount of light."""
    from ladybug_geometry.geometry3d.face import Face3D
    from ladybug_geometry.geometry3d.pointvector import Point3D

    matrix = matrix or light_obj.matrix_world
    light = light_obj.data
    kind = getattr(light, "type", "POINT")

    if kind == "AREA":
        width = getattr(light, "size", size)
        height = getattr(light, "size_y", width) if getattr(light, "shape", "SQUARE") in {
            "RECTANGLE",
            "ELLIPSE",
        } else width
    else:
        width = height = size

    half_w, half_h = width / 2.0, height / 2.0
    corners = [
        (-half_w, -half_h),
        (half_w, -half_h),
        (half_w, half_h),
        (-half_w, half_h),
    ]
    points = []
    for x, y in corners:
        world = matrix @ mathutils.Vector((x, y, 0.0))
        points.append(Point3D(world.x, world.y, world.z))
    try:
        return Face3D(points)
    except Exception:
        return None


def light_shades(lights, prefix="light"):
    """Emitting surfaces for Radiance.

    Each becomes a Shade carrying a `light` modifier: that is how Radiance is
    told a surface emits rather than reflects. Returns the shades and a report
    of where each flux figure came from."""
    if not AVAILABLE:
        return [], {}
    from honeybee.shade import Shade
    from honeybee_radiance.modifier.material import Light

    shades = []
    report = {}
    for index, entry in enumerate(lights):
        light_obj, matrix = entry if isinstance(entry, tuple) else (entry, None)
        face3d = light_geometry(light_obj, matrix)
        if face3d is None:
            continue
        lumens, origin = light_flux(light_obj)
        if lumens <= 0:
            continue
        try:
            area = max(face3d.area, 1e-4)
            # Radiance radiance, roughly: lumens spread over the emitting area
            # and over a hemisphere.
            value = lumens / (area * 179.0 * math.pi)
            colour = getattr(light_obj.data, "color", (1.0, 1.0, 1.0))
            modifier = Light(
                f"{prefix}_{index}_mod",
                r_emittance=value * colour[0],
                g_emittance=value * colour[1],
                b_emittance=value * colour[2],
            )
            shade = Shade(f"{prefix}_{index}", face3d)
            shade.properties.radiance.modifier = modifier
            shades.append(shade)
            report[light_obj.name] = f"{lumens:.0f} lm ({origin})"
        except Exception as e:
            print(f"[RefOps.honeybee] luz '{light_obj.name}': {e}")
    return shades, report


def lights_in_spaces(lights, spaces):
    """Keep only the lights standing inside the given spaces.

    Containment is decided by ray parity against the space's own mesh, the same
    test the space graph uses — a bounding box would claim a lamp on the floor
    above. This is what stops one storey's lighting from leaking into another's
    study, semantically rather than by hoping the slab is watertight."""
    import refops_api

    bim = refops_api.bim_helpers
    if not bim.ensure() or not spaces:
        return lights

    volumes = []
    for space in spaces:
        obj = bim.object_for(space)
        if obj is None or obj.data is None or not obj.data.polygons:
            continue
        triangles = []
        matrix = obj.matrix_world
        mesh = obj.data
        for polygon in mesh.polygons:
            points = [matrix @ mesh.vertices[i].co for i in polygon.vertices]
            for position in range(1, len(points) - 1):
                triangles.append((points[0], points[position], points[position + 1]))
        if triangles:
            volumes.append(triangles)

    if not volumes:
        return lights

    direction = mathutils.Vector((0.5773, 0.5774, 0.5773))
    kept = []
    for entry in lights:
        light_obj, matrix = entry if isinstance(entry, tuple) else (entry, None)
        point = (matrix or light_obj.matrix_world).translation
        for triangles in volumes:
            crossings = 0
            for a, b, c in triangles:
                if mathutils.geometry.intersect_ray_tri(a, b, c, direction, point, True):
                    crossings += 1
            if crossings % 2 == 1:
                kept.append(entry)
                break
    return kept


def _resolve_lights(options, rooms, ifc_file):
    """Which lights take part, per the chosen scope.

    ANALYSED keeps only the lamps standing inside the spaces being modelled,
    which is the answer to one storey's lighting bleeding into another's study:
    the filter is semantic, so it holds even where the slab does not."""
    options = options or {}
    mode = options.get("lights", "NONE")
    if mode == "NONE":
        return []

    if mode == "LINKED":
        lights = linked_lights(ifc_file)
    else:
        lights = blender_lights(options.get("light_collection"))

    if options.get("light_scope") == "ANALYSED":
        spaces = []
        import refops_api

        bim = refops_api.bim_helpers
        for _room, obj in rooms:
            entity = bim.entity(obj)
            if entity is not None:
                spaces.append(entity)
        lights = lights_in_spaces(lights, spaces)
    return lights


def _in_storey(space, storey):
    for relation in getattr(space, "Decomposes", None) or []:
        if relation.RelatingObject == storey:
            return True
    for relation in getattr(space, "ContainedInStructure", None) or []:
        if relation.RelatingStructure == storey:
            return True
    return False


def _opening_hosts(ifc_file):
    hosts = []
    for class_name in ("IfcWall", "IfcRoof", "IfcSlab"):
        try:
            hosts.extend(ifc_file.by_type(class_name))
        except Exception:
            continue
    return hosts


def _largest_face(obj, lb):
    """The void's biggest face, in world space — the one in the wall plane."""
    best, best_area = None, 0.0
    for face3d, _index in lb.faces_from_object(obj):
        try:
            area = face3d.area
        except Exception:
            continue
        if area > best_area:
            best, best_area = face3d, area
    return best


def _punch(rooms, outline, factory, name, tolerance=0.05):
    """Add the outline as an aperture on the room face it lies in."""
    centre = outline.center
    for room, _obj in rooms:
        for face in room.faces:
            plane = face.geometry.plane
            if abs(plane.distance_to_point(centre)) > tolerance:
                continue
            if not face.geometry.is_point_on_face(centre, tolerance):
                continue
            try:
                aperture = factory(name, outline)
                face.add_aperture(aperture)
                return aperture
            except Exception as e:
                print(f"[RefOps.honeybee] abertura '{name}': {e}")
                return None
    return None


def _filling_of(opening):
    """The door or window sitting in a void, if any — its glass is what light
    passes through, not the hole."""
    for relation in getattr(opening, "HasFillings", None) or []:
        element = getattr(relation, "RelatedBuildingElement", None)
        if element is not None:
            return element
    return None


def _apply_modifier(target, element, options):
    modifier, sources = radiance_modifier(
        element,
        f"mod_{element.id()}",
        overrides=(options or {}).get("overrides"),
        outermost=(options or {}).get("outermost", True),
    )
    if modifier is None:
        return
    try:
        target.properties.radiance.modifier = modifier
    except Exception as e:
        print(f"[RefOps.honeybee] modificador não aplicado: {e}")
        return
    if (options or {}).get("report") and sources:
        print(f"[RefOps.honeybee] {element.is_a()} {element.id()}: {sources}")


def _apply_room_modifiers(rooms, hosts, options, tolerance=0.05):
    """Give each room face the optics of the element it coincides with.

    Matched by plane rather than by name: a space face and the wall bounding it
    describe the same surface, and geometry is the only thing they reliably
    share."""
    import refops_api

    bim = refops_api.bim_helpers
    lb = refops_api.ladybug_helpers

    planes = []
    for host in hosts:
        obj = bim.object_for(host)
        if obj is None or obj.data is None:
            continue
        for face3d, _index in lb.faces_from_object(obj):
            planes.append((face3d, host))

    for room, _obj in rooms:
        for face in room.faces:
            centre = face.geometry.center
            for face3d, host in planes:
                try:
                    if abs(face3d.plane.distance_to_point(centre)) > tolerance:
                        continue
                    if not face3d.is_point_on_face(centre, tolerance):
                        continue
                except Exception:
                    continue
                _apply_modifier(face, host, options)
                break


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
                print(f"[RefOps.honeybee] abertura não anexada: {e}")
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
        print("[RefOps.honeybee] lbt-recipes não está instalado.")
        return None
    if radiance_path() is None:
        print(
            "[RefOps.honeybee] Radiance não encontrado no PATH — as receitas "
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
            f"[RefOps.honeybee] Falta o pacote '{e.name}'. Instale no console:\n"
            "    import subprocess, sys\n"
            "    subprocess.run([sys.executable, '-m', 'pip', 'install',\n"
            "        'pollination-handlers', 'honeybee-display',\n"
            "        'honeybee-radiance-postprocess',\n"
            "        '--target', r'<pasta site-packages do Bonsai>'])"
        )
        return None
    except Exception as e:
        print(f"[RefOps.honeybee] receita '{name}' falhou: {e}")
        traceback.print_exc()
        return None


def recipe_output(name, project_folder, output_name):
    if not RECIPES_AVAILABLE or project_folder is None:
        return None
    try:
        recipe = recipes.Recipe(name)
        return recipe.output_value_by_name(output_name, project_folder)
    except Exception as e:
        print(f"[RefOps.honeybee] saída '{output_name}': {e}")
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
        print(f"[RefOps.honeybee] modelo ilegível: {e}")
        return
    print(
        f"[RefOps.honeybee] Modelo: {faces} faces, {apertures} aberturas, "
        f"{doors} portas, {shades} sombras (contexto), {sensor_count} sensores."
    )
    if not shades:
        print("[RefOps.honeybee] Sem geometria de contexto no modelo.")
    if not apertures:
        print(
            "[RefOps.honeybee] Sem aberturas: um modelo fechado não recebe luz "
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
        print(f"[RefOps.honeybee] resultado '{path}': {e}")
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
