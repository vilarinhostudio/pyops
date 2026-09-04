"""Shared Bonsai/IFC plumbing for BIM operations (walls, columns, slabs, ...).

Import it from an operator file through the addon's stable alias, so the code
doesn't break if the addon folder is renamed:

    import refops_api
    bim = refops_api.bim_helpers

Everything degrades to a no-op or None when Bonsai isn't installed — check
bim.AVAILABLE first and return False from your operation.

The two rules this module encodes, learned the hard way:
  * An IFC entity created without a linked Blender object is invisible to
    Bonsai — no parametric editing, no collection, no cleanup. Always go
    through create_product / create_type.
  * Bonsai's core signatures move between versions. call_core binds arguments
    by name against the real signature instead of guessing positions.
"""

import importlib
import inspect
import sys
import traceback

import bpy

# Bonsai is resolved on FIRST USE, not at import time.
#
# RefOps registers when Blender starts. If Bonsai is enabled after that — which
# it has to be when Bonsai errors during startup and you toggle it by hand — a
# module-level `import bonsai.tool` here would already have failed, and Python
# caches that failure in sys.modules for the rest of the session. The result is
# "Bonsai is not installed" reported forever, even with Bonsai plainly running.
tool = None
ifcopenshell = None
core_root = None
core_geometry = None
core_style = None

AVAILABLE = False
STYLE_AVAILABLE = False
IMPORT_ERROR = None
PACKAGE = None

# The package is not always called "bonsai". A PR/alpha build installs itself as
# "bonsaiPR", and an extension is also reachable as bl_ext.<repo>.<id> — where,
# depending on the build, the code either lives inside the extension folder or
# in a wheel package next to it. Hardcoding one spelling is what broke here:
# ModuleNotFoundError: No module named 'bonsai' with Bonsai plainly running.
KNOWN_PACKAGES = ("bonsai", "bonsaiPR")


def _package_candidates():
    """Import roots that might be Bonsai, most likely first.

    Anything already loaded wins — if Blender enabled it, its name is the right
    one — and the known spellings follow for the case where nothing is loaded
    yet. Only roots: a package whose name is a plain top-level one, or the
    bl_ext.<repo>.<id> alias of an extension. Candidates are guesses; ensure()
    settles it by actually importing `<candidate>.tool`."""
    names = []
    for name, module in list(sys.modules.items()):
        if module is None or not getattr(module, "__path__", None):
            continue
        if "." in name and not name.startswith("bl_ext."):
            continue
        if name.rsplit(".", 1)[-1].lower().startswith("bonsai"):
            names.append(name)
    for name in KNOWN_PACKAGES:
        if name not in names:
            names.append(name)
    return names


def ensure(force=False):
    """Try to bind Bonsai. Cheap and idempotent once it succeeds — call it at the
    top of any operation that needs IFC, instead of reading AVAILABLE directly."""
    global tool, ifcopenshell, core_root, core_geometry, core_style
    global AVAILABLE, STYLE_AVAILABLE, IMPORT_ERROR, PACKAGE

    if AVAILABLE and not force:
        return True

    last_error = None
    for package in _package_candidates():
        try:
            # .tool first — it sets up the package, and importing a core
            # submodule ahead of it has bitten us before.
            _tool = importlib.import_module(f"{package}.tool")
            _root = importlib.import_module(f"{package}.core.root")
            _geometry = importlib.import_module(f"{package}.core.geometry")
            import ifcopenshell as _ifcopenshell
            import ifcopenshell.api
            import ifcopenshell.util.element
            import ifcopenshell.util.representation
            import ifcopenshell.guid
        except Exception:
            last_error = traceback.format_exc()
            continue
        break
    else:
        AVAILABLE = False
        IMPORT_ERROR = last_error or "Nenhum pacote Bonsai encontrado."
        return False

    tool = _tool
    core_root = _root
    core_geometry = _geometry
    ifcopenshell = _ifcopenshell
    PACKAGE = package
    AVAILABLE = True
    IMPORT_ERROR = None

    # Only sync_material_style needs this. Missing style support means walls
    # come out uncoloured, not that Bonsai is unusable.
    try:
        core_style = importlib.import_module(f"{PACKAGE}.core.style")
        STYLE_AVAILABLE = True
    except Exception:
        core_style = None
        STYLE_AVAILABLE = False

    return True


def status():
    """Diagnostic. From the Blender console:
    import refops_api; print(refops_api.bim_helpers.status())"""
    ensure()
    return {
        "AVAILABLE": AVAILABLE,
        "PACKAGE": PACKAGE,
        "STYLE_AVAILABLE": STYLE_AVAILABLE,
        "IMPORT_ERROR": IMPORT_ERROR,
    }


# Custom pset used to mark which RefOps container produced an element, so a
# refresh can find and remove its own output — including entities whose Blender
# object has already been deleted.
FAMILY_PSET = "RefOps"
TOL = 1e-4


def call_core(fn, **kwargs):
    """Call a bonsai.core function, binding tool objects and flags by name.
    Unknown kwargs are dropped rather than raising, so a flag that disappeared
    in a newer Bonsai doesn't break the call."""
    tools = {
        "ifc": tool.Ifc,
        "geometry": tool.Geometry,
        "root": tool.Root,
        "collector": tool.Collector,
        "blender": tool.Blender,
        "style": tool.Style,
        "spatial": tool.Spatial,
    }
    params = inspect.signature(fn).parameters
    args = [tools[n] for n in params if n in tools]
    return fn(*args, **{k: v for k, v in kwargs.items() if k in params})


def get_file():
    return tool.Ifc.get() if ensure() else None


def run(api_name, ifc_file, **kwargs):
    """Shorthand for ifcopenshell.api.run, so operator files don't need their
    own ifcopenshell import just to reach one uncovered API call."""
    return ifcopenshell.api.run(api_name, ifc_file, **kwargs)


def entity(obj):
    return tool.Ifc.get_entity(obj) if obj and ensure() else None


def family_key(container_obj, discriminator=""):
    """Stable identity for everything one container produced.

    parent + operation is NOT enough on its own: two wall operations hanging off
    the same reference mesh — one reading `wall`, another reading `wall_2` —
    share both, so the second run's cleanup deleted the first run's elements.
    The discriminator is whatever makes the run distinct, normally the selection
    attribute, and it is stable across refreshes because the user typed it.

    Still keyed off the parent's *name*: renaming the parent orphans the
    previous output."""
    parts = [container_obj.proc_parent_name, container_obj.procedural_op_type]
    if discriminator:
        parts.append(str(discriminator))
    return "::".join(parts)


def instance_from_type(type_entity, matrix=None):
    """An occurrence created by Bonsai's own type-instance operator.

    This matters for doors and windows: an occurrence built by hand gets a plain
    swept body, while the one Bonsai makes carries BIMDoorProperties and the
    parametric geometry the Parametric Geometry panel edits. Falls back to None
    so the caller can build a plain one."""
    if not ensure() or type_entity is None:
        return None, None
    props = getattr(bpy.context.scene, "BIMModelProperties", None)
    if props is None:
        return None, None

    # relating_type_id is an enum whose items are filtered by the class
    # currently set on the panel — assigning a door type while it still lists
    # walls fails with "enum not found". The class has to be set first.
    occurrence_class = type_entity.is_a()
    if occurrence_class.endswith("Type"):
        occurrence_class = occurrence_class[:-4]

    previous_class = getattr(props, "ifc_class", None)
    for attribute in ("ifc_class", "ifc_product"):
        if not hasattr(props, attribute):
            continue
        try:
            setattr(props, attribute, occurrence_class)
            break
        except Exception:
            continue

    previous = getattr(props, "relating_type_id", None)
    try:
        props.relating_type_id = str(type_entity.id())
    except Exception as e:
        print(f"[refops.bim] relating_type_id ({occurrence_class}): {e}")
        if previous_class is not None and hasattr(props, "ifc_class"):
            try:
                props.ifc_class = previous_class
            except Exception:
                pass
        return None, None

    operator = (
        find_operator("add", "type", "instance")
        or find_operator("add", "occurrence")
        or find_operator("add", "constr")
    )
    if operator is None:
        print("[refops.bim] Nenhum operador de instância de tipo nesta versão.")
        return None, None

    before = set(bpy.data.objects)
    try:
        result = operator()
        if "FINISHED" not in result:
            print(f"[refops.bim] instância de tipo -> {result}")
            return None, None
    except Exception as e:
        print(f"[refops.bim] instância de tipo: {e}")
        return None, None
    finally:
        for attribute, value in (
            ("relating_type_id", previous),
            ("ifc_class", previous_class),
        ):
            if value is None:
                continue
            try:
                setattr(props, attribute, value)
            except Exception:
                pass

    created = [obj for obj in bpy.data.objects if obj not in before]
    if not created:
        return None, None
    obj = created[0]
    if matrix is not None:
        obj.matrix_world = matrix
    return obj, tool.Ifc.get_entity(obj)


# Where optical numbers live, most specific first. Pset_MaterialOptical is the
# standard set IFC defines for exactly this, which is why it outranks anything
# improvised.
OPTICAL_ALIASES = {
    "reflectance": (
        "VisibleReflectanceFront",
        "SolarReflectanceFront",
        "Reflectance",
    ),
    "transmittance": (
        "VisibleTransmittance",
        "SolarTransmittance",
        "Transmittance",
    ),
    "specularity": ("Specularity", "SpecularReflectance"),
    "roughness": ("Roughness",),
}


def material_properties(material):
    """Every property set attached to an IfcMaterial, flattened.

    A material carries its own IfcMaterialProperties — the optical numbers
    belong to the glass or the concrete, not to one wall — so this is the
    natural home for them and the reason an element rarely needs to state
    them."""
    if material is None or not ensure():
        return {}
    values = {}
    try:
        for definition in getattr(material, "HasProperties", None) or []:
            for prop in getattr(definition, "Properties", None) or []:
                name = getattr(prop, "Name", None)
                value = getattr(prop, "NominalValue", None)
                if name and value is not None:
                    values[name] = getattr(value, "wrappedValue", value)
    except Exception as e:
        print(f"[refops.bim] propriedades do material: {e}")
    return values


def element_materials(element, outermost=True):
    """The materials of an element, ordered from the outside in.

    A layer set means several materials in one wall, and only the finish layer
    matters to light. Which end is 'outside' depends on the layer order the type
    was built with, so the caller chooses."""
    if element is None or not ensure():
        return []
    try:
        material = ifcopenshell.util.element.get_material(element)
    except Exception:
        return []
    if material is None:
        return []

    if material.is_a("IfcMaterial"):
        return [material]

    layers = getattr(material, "MaterialLayers", None)
    if layers is None:
        usage = getattr(material, "ForLayerSet", None)
        layers = getattr(usage, "MaterialLayers", None) if usage else None
    if layers:
        materials = [layer.Material for layer in layers if layer.Material]
        return materials if outermost else list(reversed(materials))

    profiles = getattr(material, "MaterialProfiles", None)
    if profiles:
        return [profile.Material for profile in profiles if profile.Material]

    constituents = getattr(material, "MaterialConstituents", None)
    if constituents:
        return [item.Material for item in constituents if item.Material]
    return []


def surface_style_optics(element):
    """Reflectance and transmittance inferred from the display style.

    A colour is not a reflectance, so the diffuse colour is converted by
    luminance — the weights are the ones the eye applies, and the result is at
    least a defensible number rather than a channel picked at random.

    This is the last resort: it describes how the element LOOKS, not what it
    was measured to do."""
    if element is None or not ensure():
        return {}
    try:
        styles = ifcopenshell.util.element.get_styles(element)
    except Exception:
        styles = []

    for style in styles or []:
        for item in getattr(style, "Styles", None) or [style]:
            if not item.is_a("IfcSurfaceStyleRendering"):
                continue
            values = {}
            colour = getattr(item, "DiffuseColour", None)
            if colour is not None and colour.is_a("IfcColourRgb"):
                values["reflectance"] = round(
                    0.265 * colour.Red + 0.670 * colour.Green + 0.065 * colour.Blue,
                    4,
                )
            transparency = getattr(item, "Transparency", None)
            if transparency is not None:
                values["transmittance"] = round(float(transparency), 4)
            specular = getattr(item, "SpecularHighlight", None)
            if specular is not None:
                raw = getattr(specular, "wrappedValue", specular)
                try:
                    values["specularity"] = round(float(raw), 4)
                except (TypeError, ValueError):
                    pass
            if values:
                return values
    return {}


def optical_properties(element, overrides=None, outermost=True):
    """Optical numbers for an element, most specific source winning.

    element pset -> material properties -> surface style -> nothing.

    Each entry comes back as (value, source) so the caller can say where a
    number came from. A simulation quietly run on a fallback is worse than one
    that refuses, and the only way to tell them apart is to carry the origin."""
    if element is None or not ensure():
        return {}

    resolved = {}

    # 1. explicit overrides, given as "Pset.Property" on the element
    for key, path in (overrides or {}).items():
        if not path:
            continue
        value = pset_value(element, path)
        if value is not None:
            resolved[key] = (value, f"pset {path}")

    # 2. the element's own property sets, by the standard names
    try:
        psets = ifcopenshell.util.element.get_psets(element)
    except Exception:
        psets = {}
    for key, names in OPTICAL_ALIASES.items():
        if key in resolved:
            continue
        for pset_name, values in psets.items():
            for name in names:
                if name in values:
                    resolved[key] = (values[name], f"{pset_name}.{name}")
                    break
            if key in resolved:
                break

    # 3. the material — where these properties belong
    for material in element_materials(element, outermost):
        properties = material_properties(material)
        if not properties:
            continue
        for key, names in OPTICAL_ALIASES.items():
            if key in resolved:
                continue
            for name in names:
                if name in properties:
                    resolved[key] = (
                        properties[name],
                        f"material {material.Name}.{name}",
                    )
                    break
        if len(resolved) == len(OPTICAL_ALIASES):
            break

    # 4. the display style, converted
    style_values = surface_style_optics(element)
    for key, value in style_values.items():
        resolved.setdefault(key, (value, "surface style"))

    return resolved


def pset_value(element, path):
    """Read "Pset_Name.PropertyName" off an element."""
    if not path or "." not in path:
        return None
    pset_name, property_name = path.split(".", 1)
    try:
        psets = ifcopenshell.util.element.get_psets(element)
    except Exception:
        return None
    return psets.get(pset_name, {}).get(property_name)


def parametric_size(entity):
    """(width, height) read from a Bonsai parametric type's BBIM pset.

    With "Use Type As-Is" the type already states the size, so asking the user
    for it again is asking the same question twice. An occurrence is followed to
    its type first, since that is where the numbers live."""
    if entity is None or not ensure():
        return 0.0, 0.0
    candidates = [entity]
    try:
        related = ifcopenshell.util.element.get_type(entity)
        if related is not None:
            candidates.append(related)
    except Exception:
        pass

    for candidate in candidates:
        try:
            psets = ifcopenshell.util.element.get_psets(candidate)
        except Exception:
            continue
        for name, values in psets.items():
            if not name.startswith("BBIM_"):
                continue
            width = values.get("overall_width") or values.get("OverallWidth")
            height = values.get("overall_height") or values.get("OverallHeight")
            if width and height:
                return float(width), float(height)

    # IfcDoor and IfcWindow carry these as plain attributes too.
    for candidate in candidates:
        width = getattr(candidate, "OverallWidth", None)
        height = getattr(candidate, "OverallHeight", None)
        if width and height:
            return float(width), float(height)
    return 0.0, 0.0


def occurrence_from_type(type_entity, matrix=None, name=None):
    """An occurrence of a type, without going through the panel.

    Bonsai's type-instance operator reads the type from BIMModelProperties, and
    that enum is filtered by whatever the panel is currently showing — assigning
    a furniture type while it lists two wall types fails with "enum not found",
    no matter what class is set first. Driving the UI from code to place an
    element was the wrong dependency.

    Building the occurrence through the IFC API instead is deterministic:
    create_entity, assign_type — which maps the type's representation onto the
    occurrence — then let Bonsai build the mesh from that representation.
    The operator is still tried first, since it also carries the parametric
    properties for doors and windows."""
    if not ensure() or type_entity is None:
        return None, None

    obj, element = instance_from_type(type_entity, matrix)
    if element is not None:
        return obj, element

    ifc_file = get_file()
    if ifc_file is None:
        return None, None

    class_name = type_entity.is_a()
    occurrence_class = class_name[:-4] if class_name.endswith("Type") else class_name
    label = name or (type_entity.Name or occurrence_class)

    obj, element = create_product(occurrence_class, label, matrix)
    if element is None:
        return None, None

    try:
        ifcopenshell.api.run(
            "type.assign_type",
            ifc_file,
            related_objects=[element],
            relating_type=type_entity,
        )
    except Exception as e:
        print(f"[refops.bim] type.assign_type: {e}")

    # assign_type maps the type's representation onto the occurrence; the mesh
    # is built from whatever ended up there.
    try:
        representation = ifcopenshell.util.representation.get_representation(
            element, "Model", "Body", "MODEL_VIEW"
        )
    except Exception:
        representation = None

    if representation is not None:
        set_representation(ifc_file, obj, element, representation)
    else:
        print(
            f"[refops.bim] '{label}' ficou sem corpo: o tipo não tem "
            "representação mapeada."
        )
        file_object(obj)
    return obj, element


def filling_type_of(entity):
    """The type to assign to an opening's filling, from whatever was pointed at.

    A type (IfcDoorType) is used as-is. An occurrence (IfcDoor) is far easier to
    pick in the outliner, so its own type is looked up and used instead — and
    when it has none, the class alone is enough to build the filling."""
    if entity is None:
        return None, None
    name = entity.is_a()
    if name.endswith("Type"):
        return entity, name[:-4]
    try:
        related = ifcopenshell.util.element.get_type(entity)
    except Exception:
        related = None
    return related, name


# --- BLENDER SIDE ---


def select(objs, active=None):
    """Select a set of objects and make one of them active.

    The active object is selected too. Setting view_layer.objects.active alone
    leaves it unselected, so an operator that polls for "two selected objects"
    sees only one and refuses — which is exactly why bim.add_opening kept
    reporting "Select openings and a target element"."""
    bpy.ops.object.select_all(action="DESELECT")
    chosen = list(objs)
    if active is not None and active not in chosen:
        chosen.append(active)
    for obj in chosen:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active or (chosen[0] if chosen else None)


def unlink_from_scene_root(obj):
    """Collector.assign adds the object to the IFC collection without removing
    it from wherever it was created, so it shows up twice in the outliner. The
    length check keeps the object visible if the Collector filed it nowhere."""
    root = bpy.context.scene.collection
    if root in obj.users_collection and len(obj.users_collection) > 1:
        root.objects.unlink(obj)


def file_object(obj):
    tool.Collector.assign(obj)
    unlink_from_scene_root(obj)


def file_in_collection(obj, name):
    """File a Bonsai-managed object into a NAMED CHILD of wherever the Collector
    put it — for a type, a subfolder of the types collection.

    Blender-side only: the outliner grouping carries no IFC meaning, which is
    why this runs after the Collector instead of replacing it. Bonsai may pull
    the object back up to the parent collection on its next pass; the next
    refresh puts it back."""
    file_object(obj)
    if not name:
        return None
    parent = next(iter(obj.users_collection), bpy.context.scene.collection)
    child = parent.children.get(name)
    if child is None:
        child = bpy.data.collections.get(name) or bpy.data.collections.new(name)
        try:
            parent.children.link(child)
        except RuntimeError as e:
            # Already linked elsewhere in the tree — leave it there rather than
            # duplicating it under the types collection.
            print(f"[RefOps.bim] coleção '{name}': {e}")
            child = None
    if child is None:
        return None
    for collection in list(obj.users_collection):
        if collection is not child:
            collection.objects.unlink(obj)
    if obj.name not in child.objects:
        child.objects.link(obj)
    return child


# --- ENTITY LIFECYCLE ---


def assign_ifc_class(
    obj, ifc_class, predefined_type=None, add_representation=False, context=None
):
    """Link an EXISTING Blender object to a new IFC occurrence.

    add_representation=True asks Bonsai to build the body from the object's
    current mesh — use it when the geometry was modelled in Blender rather than
    generated from IFC parameters. It requires the representation context;
    assign_class asserts on a missing one rather than defaulting. Note it reads
    the mesh datablock, not the evaluated one, so live modifiers are not
    captured."""
    if add_representation and context is None:
        raise ValueError(
            "assign_ifc_class(add_representation=True) needs a body context — "
            "pass context=get_body_context(ifc_file)"
        )
    return call_core(
        core_root.assign_class,
        obj=obj,
        ifc_class=ifc_class,
        predefined_type=predefined_type,
        context=context,
        should_add_representation=add_representation,
    ) or tool.Ifc.get_entity(obj)


def object_for(element):
    """Current Blender object for an IFC entity.

    Bonsai operators can delete and recreate an object, so a Python reference
    held across one of them may point at something no longer in the scene. The
    entity is the stable identity; re-resolve through it after calling anything
    that regenerates geometry."""
    if not element or not ensure():
        return None
    obj = tool.Ifc.get_object(element)
    return obj if isinstance(obj, bpy.types.Object) else None


def link_object(element, name):
    """Give an IFC entity a Blender object and register the link.

    A type read back from an IFC file has no object: Bonsai lists it in the
    Type Manager straight from the file and only builds one when something asks
    for it in the scene. Everything that writes a representation needs a real
    object, so an adopted type gets one here instead of being rebuilt — see the
    update rules in op_bim_type_geometry.

    The object is named Bonsai's way, "<Class>/<Name>". Its rename callback
    reads the element's name back off the object name, so anything else gets
    rewritten — and a bare "Cube" collides with the reference object, leaving
    Blender's "Cube.001" to be written into the IFC as the entity's name."""
    obj = bpy.data.objects.new(
        f"{element.is_a()}/{name}", bpy.data.meshes.new(name)
    )
    bpy.context.scene.collection.objects.link(obj)
    try:
        tool.Ifc.link(element, obj)
    except Exception as e:
        print(f"[RefOps.bim] tool.Ifc.link falhou para '{name}': {e}")
        bpy.data.objects.remove(obj, do_unlink=True)
        return None
    file_object(obj)
    return obj


def create_product(ifc_class, name, matrix=None, predefined_type=None):
    """New Blender object + linked IFC occurrence, with no representation yet.
    Returns (obj, element) or (None, None)."""
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(obj)
    if matrix is not None:
        obj.matrix_world = matrix

    element = assign_ifc_class(obj, ifc_class, predefined_type)
    if not element:
        bpy.data.objects.remove(obj, do_unlink=True)
        return None, None
    return obj, element


def create_type(ifc_file, ifc_class, name, predefined_type=None):
    """Types need a Blender object too, or they never appear in the
    IfcTypeProduct collection or the Type Manager. assign_class is the wrong
    tool here — it expects a product — so the link is made explicitly."""
    kwargs = {"ifc_class": ifc_class, "name": name}
    if predefined_type:
        kwargs["predefined_type"] = predefined_type
    try:
        entity = ifcopenshell.api.run("root.create_entity", ifc_file, **kwargs)
    except Exception as e:
        # An IFC class whose type counterpart doesn't exist in the schema —
        # the element is still valid, it just goes untyped.
        print(f"[refops.bim] '{ifc_class}' não existe no schema: {e}")
        return None, None
    if not entity:
        return None, None
    return link_object(entity, name), entity


def set_representation(ifc_file, obj, element, representation):
    """Assign the representation and let Bonsai rebuild the Blender mesh from
    it — this is what makes TAB open the parametric editor instead of plain
    mesh edit mode."""
    ifcopenshell.api.run(
        "geometry.assign_representation",
        ifc_file,
        product=element,
        representation=representation,
    )
    show_representation(obj, representation)


def show_representation(obj, representation):
    """Rebuild a Blender object's mesh from an IFC representation, WITHOUT
    assigning it.

    Split out of set_representation because an UPDATE must not assign again:
    assigning a representation to a type mints another IfcRepresentationMap, and
    every element already placed references the first one."""
    if obj is None or representation is None:
        return
    # switch_representation swaps obj.data for a mesh built from the IFC, which
    # orphans the placeholder created with the object. One per element per
    # refresh, and Blender never collects it on its own.
    placeholder = obj.data
    try:
        call_core(
            core_geometry.switch_representation,
            obj=obj,
            representation=representation,
            apply_openings=True,
        )
    except Exception as e:
        print(f"[RefOps.bim] switch_representation em '{obj.name}': {e}")
        return
    if placeholder and placeholder is not obj.data:
        try:
            if not placeholder.users and not placeholder.use_fake_user:
                bpy.data.meshes.remove(placeholder)
        except (ReferenceError, TypeError):
            pass
    file_object(obj)


def representation_in_context(product, context):
    """The product's representation in one context, or None.

    A type carries IfcRepresentationMaps and an occurrence an
    IfcProductDefinitionShape, so the two have to be looked at separately. The
    match is on the context ENTITY, not on its type/identifier/target triple:
    a file can hold two contexts answering to the same triple, and picking the
    wrong one writes the plan symbol over the body."""
    for rep_map in getattr(product, "RepresentationMaps", None) or []:
        rep = getattr(rep_map, "MappedRepresentation", None)
        if rep is not None and rep.ContextOfItems == context:
            return rep
    shape = getattr(product, "Representation", None)
    for rep in getattr(shape, "Representations", None) or []:
        if rep.ContextOfItems == context:
            return rep
    return None


def mesh_representation(ifc_file, context, obj, mesh=None):
    """A representation built from a Blender mesh, in whatever form the context
    asks for — Tessellation under Model/Body/MODEL_VIEW, a curve set under a
    plan or section context.

    add_representation reads the representation type off the context itself,
    which is why nothing here names one. Only the three arguments that never
    moved are passed: the rest of that signature has changed between
    IfcOpenShell versions and the defaults are what we want anyway."""
    # Listeners only for an object Bonsai knows. The one post-listener on
    # add_representation is generate_box, which does `assert product` on
    # tool.Ifc.get_entity(blender_object) — a bare assert, which is how this
    # failure showed up as an exception with an empty message. A throwaway
    # carrier is linked to nothing, and has no bounding box worth generating.
    linked = tool.Ifc.get_entity(obj) is not None
    try:
        return ifcopenshell.api.run(
            "geometry.add_representation",
            ifc_file,
            should_run_listeners=linked,
            context=context,
            blender_object=obj,
            geometry=mesh if mesh is not None else obj.data,
        )
    except Exception as e:
        # Traceback, not just str(e): the failure comes from deep inside
        # ifcopenshell's Blender-dependent add_representation, and some of what
        # it raises (a bare assert, notably) has an empty message — "erro: "
        # with nothing after it is the one report that says nothing at all.
        print(
            f"[RefOps.bim] add_representation "
            f"({getattr(context, 'ContextIdentifier', '?')}/"
            f"{getattr(context, 'TargetView', '?')}): {e!r}"
        )
        print(traceback.format_exc())
        return None


def set_mesh_representation(ifc_file, product, obj, context, mesh=None):
    """Give a product the geometry of a Blender mesh in one context, updating in
    place when it already has one there.

    Updating means swapping the Items of the EXISTING IfcShapeRepresentation,
    never assigning a fresh one: an occurrence references its type through an
    IfcMappedItem pointing at the type's IfcRepresentationMap, so a new
    representation would mint a new map and every element already placed would
    keep pointing at the old one — the very reference this exists to preserve.
    Same entity, new items."""
    new_rep = mesh_representation(ifc_file, context, obj, mesh)
    if new_rep is None:
        return None

    existing = representation_in_context(product, context)
    if existing is None:
        try:
            ifcopenshell.api.run(
                "geometry.assign_representation",
                ifc_file,
                product=product,
                representation=new_rep,
            )
        except Exception as e:
            print(f"[RefOps.bim] assign_representation: {e}")
            return None
        return new_rep

    old_items = list(existing.Items or [])
    existing.Items = new_rep.Items
    existing.RepresentationType = new_rep.RepresentationType
    # The carrier is empty now — its items live on the representation the
    # occurrences already reference.
    try:
        ifc_file.remove(new_rep)
    except Exception as e:
        print(f"[RefOps.bim] descartar representação temporária: {e}")
    for item in old_items:
        # The styled item goes first: it holds an inverse reference to the
        # geometry, and remove_deep2 keeps whatever is still referenced — the
        # old faces would survive as orphans with the style pinning them.
        for referrer in list(ifc_file.get_inverse(item)):
            if referrer.is_a("IfcStyledItem"):
                ifc_file.remove(referrer)
        try:
            ifcopenshell.util.element.remove_deep2(ifc_file, item)
        except Exception as e:
            print(f"[RefOps.bim] remover item antigo: {e}")
    return existing


def assign_type(ifc_file, element, type_entity):
    ifcopenshell.api.run(
        "type.assign_type", ifc_file, related_objects=[element], relating_type=type_entity
    )


def assign_container(ifc_file, element, structure):
    if not structure:
        return
    ifcopenshell.api.run(
        "spatial.assign_container",
        ifc_file,
        relating_structure=structure,
        products=[element],
    )


def assign_usage(ifc_file, element, usage_type):
    """e.g. 'IfcMaterialLayerSetUsage' for walls and slabs,
    'IfcMaterialProfileSetUsage' for columns and beams. The usage lives on the
    OCCURRENCE and is what makes Bonsai treat it as parametric."""
    ifcopenshell.api.run(
        "material.assign_material", ifc_file, products=[element], type=usage_type
    )


# --- PSETS ---


def dominant(faces, layer, precision=3, default=0.0, cast=float):
    """Area-weighted majority value of an attribute across a face group.

    A wall face subdivided to carve a door opening ends up as several coplanar
    faces, and the thickness or alignment is rarely painted on every one of
    them. Making the value part of the grouping key would then split one wall
    into fragments; voting on it keeps the wall whole and lets the largest
    painted area decide. Returns (value, mixed) so the caller can warn."""
    if layer is None:
        return default, False
    weights = {}
    for face in faces:
        value = cast(face[layer])
        weights[value] = weights.get(value, 0.0) + face.calc_area()
    if not weights:
        return default, False
    positive = {k: v for k, v in weights.items() if k} or weights
    winner = max(positive.items(), key=lambda item: item[1])[0]
    if isinstance(winner, float):
        winner = round(winner, precision)
    return winner, len(positive) > 1


def add_opening(ifc_file, opening, host):
    """Attach an IfcOpeningElement to the element it voids.

    The API module for this has been renamed across IfcOpenShell versions
    (void.add_opening, feature.add_feature, ...), so the candidates are tried in
    turn and, failing all of them, the relationship is written directly. The
    IfcRelVoidsElement entity itself has not changed since IFC4, which makes the
    raw path the dependable one."""
    # Bonsai's own operator first: besides writing the relationship it triggers
    # the boolean regeneration, which is why an element voided through the raw
    # API still looks uncut in the viewport.
    opening_obj = object_for(opening)
    host_obj = object_for(host)
    # The relationship is written first, without any operator. bim.add_opening
    # kept failing its poll and the reason was never visible from here, so the
    # dependable path leads: create IfcRelVoidsElement, then rebuild the host's
    # geometry so the cut shows up.
    relation = None
    for api_name in ("void.add_opening", "feature.add_feature"):
        try:
            relation = ifcopenshell.api.run(
                api_name, ifc_file, opening=opening, element=host
            )
            break
        except Exception:
            continue

    if relation is None:
        try:
            relation = ifc_file.createIfcRelVoidsElement(
                ifcopenshell.guid.new(),
                _owner_history(ifc_file),
                None,
                None,
                host,
                opening,
            )
        except Exception as e:
            print(f"[refops.bim] IfcRelVoidsElement: {e}")
            return None

    rebuild_geometry(host)
    return relation


def add_filling(ifc_file, opening, element):
    """Put a door or a window inside an opening. Same version dance."""
    relation = None
    for api_name in ("void.add_filling", "feature.add_filling"):
        try:
            relation = ifcopenshell.api.run(
                api_name, ifc_file, opening=opening, element=element
            )
            break
        except Exception:
            continue

    if relation is None:
        try:
            relation = ifc_file.createIfcRelFillsElement(
                ifcopenshell.guid.new(),
                _owner_history(ifc_file),
                None,
                None,
                opening,
                element,
            )
        except Exception as e:
            print(f"[refops.bim] IfcRelFillsElement: {e}")
            return None

    # The fill only snaps into its opening once this runs — which is exactly
    # why the door appeared correctly only after entering and leaving the
    # door editor by hand.
    filling_obj = object_for(element)
    recalculate_fill(filling_obj)
    # A door added this way keeps an empty body until its editor is opened and
    # closed once — so that is done here rather than left to the user.
    regenerate_parametric(filling_obj)
    return relation


def find_operator(*keywords):
    """First bim operator whose name contains all the keywords.

    Bonsai renames operators between releases — add_constr_type_instance simply
    does not exist here — and a hard-coded name fails with "could not be
    found". Discovering it at runtime survives the rename."""
    try:
        names = [name for name in dir(bpy.ops.bim) if not name.startswith("_")]
    except Exception:
        return None
    for name in sorted(names, key=len):
        if all(keyword in name for keyword in keywords):
            return getattr(bpy.ops.bim, name)
    return None


def rebuild_geometry(element):
    """Rebuild an element's body from its IFC representation, openings applied.

    This is what makes a void actually cut: writing IfcRelVoidsElement records
    the relationship, but the Blender mesh keeps whatever it was built with
    until the representation is switched again with apply_openings=True."""
    obj = object_for(element)
    if obj is None or not ensure():
        return False
    try:
        representation = ifcopenshell.util.representation.get_representation(
            element, "Model", "Body", "MODEL_VIEW"
        )
        if representation is None:
            return False
        call_core(
            core_geometry.switch_representation,
            obj=obj,
            representation=representation,
            apply_openings=True,
        )
        return True
    except Exception as e:
        print(f"[refops.bim] rebuild_geometry: {e}")
        return False


def opening_from_object(host, obj, preserve_placement=False):
    """Turn a plain Blender mesh into a void in `host`, using Bonsai's operator.

    This is the workflow the operator was written for: you model a dumb box and
    hand it to Bonsai together with the element, and IT creates the
    IfcOpeningElement and applies the boolean. Handing it an object that is
    ALREADY an IfcOpeningElement is why the poll kept answering "Select openings
    and a target element" — the object had stopped being a candidate opening the
    moment it became one.

    `preserve_placement` is what decides who positions a FILLING. Left False,
    Bonsai's FilledOpeningGenerator throws the filling's matrix away and writes
    the HOST's rotation onto it, plus a half turn when the point is nearer the
    layer set's side axis than its base axis. That is a good default for a door
    dragged in by hand and a disaster for a placed one: the swing then follows
    the wall's alignment instead of the input, and a bounding box measured
    before the call describes an orientation that no longer exists. Pass True
    when the caller has already put the filling exactly where it belongs.

    Returns the entity Bonsai created, found by diffing the host's voids."""
    if not ensure() or obj is None or host is None:
        return None
    host_obj = object_for(host)
    if host_obj is None:
        return None

    before = {
        relation.RelatedOpeningElement.id()
        for relation in (host.HasOpenings or [])
        if relation.RelatedOpeningElement
    }
    try:
        select([obj], host_obj)
        # Called without an execution context, so Blender runs execute() and not
        # invoke() — invoke would overwrite the flag from the shift key.
        try:
            result = bpy.ops.bim.add_opening(preserve_placement=preserve_placement)
        except TypeError:
            # Older Bonsai without the flag: it always repositions.
            result = bpy.ops.bim.add_opening()
        if "FINISHED" not in result:
            print(f"[refops.bim] bim.add_opening -> {result}")
            return None
    except Exception as e:
        print(f"[refops.bim] bim.add_opening: {e}")
        return None

    for relation in host.HasOpenings or []:
        opening = relation.RelatedOpeningElement
        if opening is not None and opening.id() not in before:
            return opening
    return None


def regenerate_parametric(obj):
    """Force Bonsai to rebuild a parametric element's body.

    Entering and cancelling the type's editor is enough — cancelling changes no
    data but the exit path rebuilds the geometry. It is the programmatic
    equivalent of the TAB-in-TAB-out that made the door finally appear."""
    if obj is None or not ensure():
        return False
    element = tool.Ifc.get_entity(obj)
    if element is None:
        return False
    suffix = element.is_a()[3:].lower()  # IfcDoor -> door
    enable = find_operator("enable", "editing", suffix)
    cancel = find_operator("cancel", "editing", suffix)
    if enable is None or cancel is None:
        return False
    try:
        select([obj], obj)
        enable()
        cancel()
        return True
    except Exception as e:
        print(f"[refops.bim] regenerate_parametric ({suffix}): {e}")
        return False


def toggle_door_swing(obj, flip_geometry=False):
    """Swap a door's opening direction, or mirror its leaf.

    Both are the same Bonsai operator: flip_geometry=False changes which way it
    swings, flip_geometry=True mirrors the geometry. Setting attributes on the
    element does nothing — the swing lives in the parametric definition and only
    this operator rebuilds it."""
    if obj is None or not ensure():
        return False
    operator = find_operator("toggle", "swing")
    if operator is None:
        print("[refops.bim] Nenhum operador de swing nesta versão.")
        return False
    try:
        select([obj], obj)
        result = operator(
            flip_geometry=flip_geometry, skip_direction_change=False
        )
        if "FINISHED" in result:
            return True
        print(f"[refops.bim] toggle_door_swing -> {result}")
    except Exception as e:
        print(f"[refops.bim] toggle_door_swing: {e}")
    return False


def capture_finish(obj):
    """Snapshot the material slots and the RefOps boolean of an object.

    Bonsai rebuilds an element's mesh whenever its representation changes —
    adding a void does exactly that — and the rebuilt mesh arrives bare. Slots
    and modifiers set beforehand are gone, which is why a wall lost its cut
    material and its cutter the moment a door was fitted.

    Captured rather than recomputed: the operator that set them may not be the
    one restoring them, and whatever was there is what should come back."""
    if obj is None or obj.data is None:
        return None
    snapshot = {
        "materials": [m.name if m else None for m in obj.data.materials],
        "indices": [polygon.material_index for polygon in obj.data.polygons],
        "links": [slot.link for slot in obj.material_slots],
        "booleans": [],
    }
    for modifier in obj.modifiers:
        if modifier.type != "BOOLEAN":
            continue
        snapshot["booleans"].append(
            {
                "name": modifier.name,
                "operation": modifier.operation,
                "object": modifier.object.name if modifier.object else None,
                "solver": getattr(modifier, "solver", "EXACT"),
                "material_mode": getattr(modifier, "material_mode", None),
            }
        )
    return snapshot


def restore_finish(obj, snapshot):
    """Put the slots and booleans back after a rebuild.

    Face indices are restored only when the face count matches: a mesh that was
    re-cut has different faces, and forcing old indices onto it would paint the
    wrong ones. The slots themselves always come back, so the new faces at least
    land on the right materials by default."""
    if obj is None or obj.data is None or not snapshot:
        return False
    mesh = obj.data

    mesh.materials.clear()
    for name in snapshot["materials"]:
        mesh.materials.append(bpy.data.materials.get(name) if name else None)
    for slot, link in zip(obj.material_slots, snapshot["links"]):
        try:
            slot.link = link
        except Exception:
            pass

    indices = snapshot["indices"]
    if len(indices) == len(mesh.polygons):
        for polygon, index in zip(mesh.polygons, indices):
            polygon.material_index = index
    elif indices:
        # The cut changed the face count. Everything goes to the body slot and
        # the boolean re-paints whatever it creates.
        body = 1 if len(mesh.materials) > 1 else 0
        for polygon in mesh.polygons:
            polygon.material_index = body
    mesh.update()

    existing = {modifier.name for modifier in obj.modifiers}
    for entry in snapshot["booleans"]:
        if entry["name"] in existing:
            continue
        cutter = bpy.data.objects.get(entry["object"]) if entry["object"] else None
        if cutter is None:
            continue
        modifier = obj.modifiers.new(name=entry["name"], type="BOOLEAN")
        modifier.operation = entry["operation"]
        modifier.object = cutter
        try:
            modifier.solver = entry["solver"]
        except Exception:
            pass
        if entry["material_mode"] is not None and hasattr(modifier, "material_mode"):
            modifier.material_mode = entry["material_mode"]
    return True


def void_elements(void_obj, elements, name="RefOps Void"):
    """Cut ONE mesh into every element of a run, as real IFC openings.

    A Blender boolean modifier would leave the cut invisible to IFC. Bonsai's
    add_opening consumes the object it is given, so each element needs its own
    copy — the source stays untouched and editable, and every copy is a genuine
    IfcOpeningElement that exports.

    Returns the openings created, so the caller can tag them for cleanup."""
    if void_obj is None or not ensure():
        return []
    openings = []
    for element in elements:
        host_obj = object_for(element)
        if host_obj is None:
            continue
        copy = void_obj.copy()
        copy.data = void_obj.data  # linked: one mesh however many hosts
        copy.name = f"{name}_{host_obj.name}"
        bpy.context.scene.collection.objects.link(copy)
        copy.matrix_world = void_obj.matrix_world.copy()
        opening = opening_from_object(element, copy)
        if opening is None:
            if copy.name in bpy.data.objects:
                bpy.data.objects.remove(copy, do_unlink=True)
            continue
        openings.append(opening)
    return openings


def recalculate_fill(obj):
    """Snap a filling into its opening.

    This is the step that was missing: a door added to a void keeps its own
    placement until the fill is recalculated, which is exactly why it only
    landed correctly after entering and leaving the door editor by hand."""
    if obj is None:
        return False
    operator = find_operator("recalculate", "fill")
    if operator is None:
        print("[refops.bim] Nenhum operador de recalculate fill nesta versão.")
        return False
    try:
        select([obj], obj)
        result = operator()
        if "FINISHED" in result:
            return True
        print(f"[refops.bim] recalculate_fill -> {result}")
    except Exception as e:
        print(f"[refops.bim] recalculate_fill: {e}")
    return False


def copy_type(ifc_file, type_entity, name):
    """A duplicate of a type, so editing its size doesn't rewrite every element
    already using the original.

    Reused by name: the name encodes the dimensions, so a second door of the
    same size finds the existing type instead of minting another."""
    existing = find_type_by_name(ifc_file, type_entity.is_a(), name)
    if existing is not None:
        return existing
    try:
        copies = ifcopenshell.api.run(
            "root.copy_class", ifc_file, product=type_entity
        )
    except Exception as e:
        print(f"[refops.bim] root.copy_class: {e}")
        return None
    new_type = copies[0] if isinstance(copies, (list, tuple)) else copies
    if new_type is None:
        return None
    try:
        ifcopenshell.api.run(
            "attribute.edit_attributes",
            ifc_file,
            product=new_type,
            attributes={"Name": name},
        )
    except Exception:
        try:
            new_type.Name = name
        except Exception as e:
            print(f"[refops.bim] renomear type: {e}")
    obj = object_for(new_type)
    if obj is not None:
        obj.name = name
        file_object(obj)
    return new_type


def set_parametric_size(ifc_file, entity, height=None, width=None):
    """Overall height and width on a Bonsai parametric type.

    Bonsai keeps these in a BBIM_* pset — BBIM_Door, BBIM_Window — and the panel
    reads them from there, so writing the pset is what actually changes the
    geometry."""
    values = {}
    if height:
        values["overall_height"] = float(height)
    if width:
        values["overall_width"] = float(width)
    if not values:
        return False

    # The pset name follows the class: IfcWindowType -> BBIM_Window,
    # IfcDoorType -> BBIM_Door. Deriving it means anything Bonsai models
    # parametrically works here without a lookup table to maintain.
    name = entity.is_a()
    base = name[3:-4] if name.endswith("Type") else name[3:]
    pset_name = f"BBIM_{base}"
    try:
        set_pset(ifc_file, entity, pset_name, values)
        return True
    except Exception as e:
        print(f"[refops.bim] {pset_name}: {e}")
        return False


def _owner_history(ifc_file):
    histories = ifc_file.by_type("IfcOwnerHistory")
    return histories[0] if histories else None


def set_pset(ifc_file, element, name, properties):
    pset = ifcopenshell.api.run("pset.add_pset", ifc_file, product=element, name=name)
    ifcopenshell.api.run(
        "pset.edit_pset", ifc_file, pset=pset, properties=properties
    )
    return pset


def tag_family(ifc_file, element, key):
    return set_pset(ifc_file, element, FAMILY_PSET, {"Family": key})


# --- STOREY AND STATUS BY ATTRIBUTE ---

# NOT USED: no operator writes a renovation status any more — it is set in
# Bonsai. The three status helpers below stay here, unreferenced, only so the
# mapping does not have to be worked out again if it comes back.

# 0 is deliberately "leave it out": an unpainted attribute reads as zero, and a
# default of Existing would silently label an entire model.
STATUS_BY_INDEX = {0: None, 1: "EXISTING", 2: "DEMOLISH", 3: "NEW"}

# The reverse, for the letter codes the operators used to carry internally.
STATUS_TO_CODE = {"EXISTING": "A", "DEMOLISH": "B", "NEW": "C", None: "N"}

_DOMAIN_LAYERS = {
    "POINT": ("verts", "int"),
    "EDGE": ("edges", "int"),
    "FACE": ("faces", "int"),
}


def int_layers(bm, name):
    """The integer layer called `name`, on whichever domain carries it.

    An operator that reads vertices needs the value on vertices and one that
    reads faces needs it on faces — asking the caller to say which would mean
    the same attribute name behaving differently per operator. Returns a dict of
    domain to layer, so the reader takes whichever matches the element it has."""
    found = {}
    if not name:
        return found
    for domain, (sequence, kind) in _DOMAIN_LAYERS.items():
        collection = getattr(bm, sequence, None)
        if collection is None:
            continue
        layers = getattr(collection.layers, kind, None)
        layer = layers.get(name) if layers else None
        if layer is not None:
            found[domain] = layer
    return found


def read_int(element, layers, domain, default=0):
    """Read an integer attribute off a bmesh element."""
    layer = layers.get(domain) if layers else None
    if layer is None:
        return default
    try:
        return int(element[layer])
    except Exception:
        return default


def storeys_by_elevation(ifc_file=None):
    """Building storeys ordered from the ground up.

    Ordered by the placement Z rather than by name or file order: an index is
    only meaningful against a physical stack, and storey names are free text."""
    if not ensure():
        return []
    ifc_file = ifc_file or get_file()
    if ifc_file is None:
        return []
    try:
        storeys = list(ifc_file.by_type("IfcBuildingStorey"))
    except Exception:
        return []

    def elevation(storey):
        value = getattr(storey, "Elevation", None)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
        obj = object_for(storey)
        return obj.matrix_world.translation.z if obj is not None else 0.0

    return sorted(storeys, key=elevation)


def storey_by_index(index, ifc_file=None, warn=True):
    """The n-th storey from the ground, 0-based.

    An index past the top returns the topmost rather than creating a floor:
    inventing a storey is the kind of thing only found later, in the schedule."""
    storeys = storeys_by_elevation(ifc_file)
    if not storeys:
        return None
    if index < 0:
        index = 0
    if index >= len(storeys):
        if warn:
            print(
                f"[refops.bim] Pavimento {index} não existe "
                f"({len(storeys)} no arquivo); usando o último."
            )
        index = len(storeys) - 1
    return storeys[index]


def resolve_storey(pointer_entity, index=None, ifc_file=None):
    """The storey an element belongs to: the pointer, or the index."""
    if index is not None:
        return storey_by_index(index, ifc_file)
    return pointer_entity


def status_from_index(index):
    """NOT USED — see the note above STATUS_BY_INDEX.

    Renovation status from an integer attribute, None when unset."""
    return STATUS_BY_INDEX.get(int(index)) if index is not None else None


def set_status(ifc_file, element, status):
    """NOT USED — see the note above STATUS_BY_INDEX.

    status: EXISTING | DEMOLISH | NEW"""
    return set_pset(ifc_file, element, "Pset_ElementStatus", {"Status": status})


# --- STRING TABLE (VERTEX GROUPS) ---


def vgroup_string(obj, index):
    """String pintável: nome do vertex group na posição `index`.

    Blender não tem atributo string, mas o nome de um vertex group é string e
    sua posição na lista é int. Pintar um índice inteiro (face, aresta, ...) e
    resolver aqui é como um campo de texto vira pintável em vez de digitado.

    Só é chamada quando o caller optou explicitamente por essa fonte (ex.:
    enum "Choose Material" em vez de "Auto"), então a ausência de vertex
    groups é erro do usuário, não estado a tolerar silenciosamente. Índice
    fora do intervalo clampa pro último grupo, mesma convenção de
    storey_by_index — útil quando o atributo foi pintado antes de um grupo
    ser removido.
    """
    groups = getattr(obj, "vertex_groups", None) if obj else None
    if not groups or len(groups) == 0:
        raise ValueError(
            f"'{obj.name if obj else '?'}' não tem nenhum vertex group; "
            "crie ao menos um antes de usar esta fonte de material."
        )
    clamped = max(0, min(int(index), len(groups) - 1))
    if clamped != index:
        print(
            f"[refops.bim] vertex group {index} não existe em "
            f"'{obj.name}' ({len(groups)} grupo(s)); usando o último."
        )
    return groups[clamped].name


# --- MATERIALS ---


def get_or_create_material(ifc_file, name):
    existing = next(
        (m for m in ifc_file.by_type("IfcMaterial") if m.Name == name), None
    )
    if existing:
        return existing
    return ifcopenshell.api.run("material.add_material", ifc_file, name=name)


# --- MATERIAL AGAINST THE TYPE ---
#
# The type is the only authority: an element reads its material set from the
# type and never builds one from a loose scene material, so an input can never
# disagree with the model. Blender materials left this path on purpose -- the
# viewport colour comes from the IFC surface style.
#
# Only the operators that MINT a type touch material at all. The ones that
# consume a type used to be able to rename its set from a painted attribute;
# that renamed a shared set project-wide from a local vote, and the name it
# wrote was decoupled from the layers underneath, so it could disagree with
# the very model it was supposed to describe. Two different names now mean two
# different types, which is what they meant in IFC all along.


def type_material_set(type_entity, set_class):
    """The IfcMaterial*Set a type carries, or None when it carries another kind.

    Note the class asked for is the SET, never the Usage: IfcMaterialProfileSetUsage
    is an occurrence entity — it is what assign_usage puts on the element — so a
    type would never answer to it and every column would be rejected."""
    if type_entity is None or not ensure():
        return None
    try:
        material = ifcopenshell.util.element.get_material(type_entity)
    except Exception:
        return None
    if material is None:
        return None
    return material if material.is_a(set_class) else None


def get_or_create_blender_material(name):
    """Get-or-create, never bpy.data.materials.new on its own: a refresh runs
    this once per element, and .new() would pile up Name.001, Name.002, ..."""
    return bpy.data.materials.get(name) or bpy.data.materials.new(name)


def resolve_type_material(type_entity, set_class):
    """The material set an element must read off its Type, or None.

    None means the caller SKIPS the element: thickness and section are read from
    this set, so without it there is nothing to build. Refusing beats guessing --
    a wall built without the layer set its type was supposed to carry is wrong in
    a way only found later, in the schedule.

    Nothing is resolved down to a single Blender material any more. The element
    inherits the whole set through IFC and the viewport takes its colour from the
    surface style, so there is no slot left to disagree with the model."""
    material_set = type_material_set(type_entity, set_class)
    if material_set is None:
        print(
            f"[RefOps.bim] '{getattr(type_entity, 'Name', None) or '?'}' não "
            f"carrega um {set_class}. Defina esse set no Type antes de rodar "
            "esta operação — nada foi criado."
        )
    return material_set


def vgroup_name_from_faces(obj, faces, layer):
    """The vertex group name a face group points at, or None when the attribute
    was left blank.

    Deliberately NOT dominant(): that helper treats zero as "unpainted" and
    prefers any non-zero value, which is right for a thickness and wrong here —
    vertex group 0 is the first group in the list, a perfectly ordinary choice.
    A face group large enough to be one wall would lose to a single stray face
    painted with index 1."""
    if layer is None:
        return None
    weights = {}
    for face in faces:
        index = int(face[layer])
        weights[index] = weights.get(index, 0.0) + face.calc_area()
    if not weights:
        return None
    winner = max(weights.items(), key=lambda item: item[1])[0]
    try:
        return vgroup_string(obj, winner)
    except ValueError as e:
        print(f"[RefOps.bim] {e}")
        return None


def layer_set_thickness(material_set):
    """Total thickness of a layer set.

    Reading it off the type is the point of the whole arrangement: an input and
    a layer set can disagree, and when they do the model states one thickness
    and the geometry another."""
    return sum(
        float(layer.LayerThickness or 0.0)
        for layer in (getattr(material_set, "MaterialLayers", None) or [])
    )


def assign_constituent_set(ifc_file, type_entity, set_name, constituent_name):
    """An IfcMaterialConstituentSet on a type the operator just built.

    The mirror image of resolve_type_material: the face component has no type to
    read from because it mints its own, so the set is created here instead —
    but assimilated by name when one already answers to it, which is what lets
    a second run converge on what the first built instead of minting a
    near-duplicate.

    A constituent set carries NO thickness. That is the whole difference from a
    layer set, and the reason the Solidify attribute stays in that operator
    while it left the wall and the slab."""
    existing = next(
        (
            candidate
            for candidate in ifc_file.by_type("IfcMaterialConstituentSet")
            if candidate.Name == set_name
        ),
        None,
    )
    if existing is None:
        existing = _create_constituent_set(ifc_file, set_name, constituent_name)
    if existing is None:
        return None

    try:
        ifcopenshell.api.run(
            "material.assign_material",
            ifc_file,
            products=[type_entity],
            type="IfcMaterialConstituentSet",
            material=existing,
        )
    except Exception as e:
        print(f"[RefOps.bim] atribuir IfcMaterialConstituentSet ao type: {e}")
        return None
    return existing


def _create_constituent_set(ifc_file, set_name, constituent_name):
    """Raw entities rather than material.add_constituent — same reasoning as
    _copy_material_set: the API name has moved, the entity has not."""
    material = get_or_create_material(ifc_file, constituent_name)
    try:
        constituent = ifc_file.create_entity(
            "IfcMaterialConstituent", Name=constituent_name, Material=material
        )
        return ifc_file.create_entity(
            "IfcMaterialConstituentSet",
            Name=set_name,
            MaterialConstituents=[constituent],
        )
    except Exception as e:
        print(f"[RefOps.bim] criar IfcMaterialConstituentSet '{set_name}': {e}")
        return None


def find_constituent_set(ifc_file, name):
    """An IfcMaterialConstituentSet ALREADY in the file, by name.

    Never creates one — the mirror of assign_constituent_set, for the operators
    that consume a set someone else authored. A name matching nothing leaves the
    entity without material, which is the honest outcome: minting a set here
    would put a material in the model no one asked for."""
    if not name:
        return None
    return next(
        (
            candidate
            for candidate in ifc_file.by_type("IfcMaterialConstituentSet")
            if candidate.Name == name
        ),
        None,
    )


def assign_material_set(ifc_file, entity, material_set):
    """Point an entity at an existing material set, replacing what it had.

    Skipped when it already carries that very set: re-running assign_material on
    every refresh churns the file, and the association it would rebuild is the
    one every element reads through."""
    if material_set is None or not ensure():
        return None
    try:
        current = ifcopenshell.util.element.get_material(entity)
    except Exception:
        current = None
    if current is not None and current.id() == material_set.id():
        return material_set
    if current is not None:
        try:
            ifcopenshell.api.run(
                "material.unassign_material", ifc_file, products=[entity]
            )
        except Exception as e:
            print(f"[RefOps.bim] material.unassign_material: {e}")
    try:
        ifcopenshell.api.run(
            "material.assign_material",
            ifc_file,
            products=[entity],
            type=material_set.is_a(),
            material=material_set,
        )
    except Exception as e:
        print(f"[RefOps.bim] material.assign_material: {e}")
        return None
    return material_set


def material_props(domain="face"):
    """The Material section, for the operators that MINT a type. Kept next to
    the functions that read these fields so the names can't drift apart.

    `domain` only changes the label: edge-driven components paint the index on
    edges, face-driven ones on faces.

    The single attribute names the material AND, through the type name, the
    set: a second field asking for another vertex group index read as a
    duplicate of the first in the UI, and nothing needed the two to differ.

    There is no shape for the type-consuming operators — see the note above
    type_material_set for why they no longer have a Material section at all."""
    return {
        "sep_material": ("Separator", {}),
        "lbl_material": ("Label", {"text": "Material", "icon": "MATERIAL"}),
        "material_attr": (
            "String",
            {
                "name": f"Material Attr ({domain} int)",
                "search": "attribute",
                "description": (
                    "Index into the reference object's vertex groups; the "
                    "group's name becomes the material of the new type"
                ),
            },
        ),
    }


def sync_material_style(ifc_file, ifc_material, blender_material, context):
    """Elements take their viewport colour from an IfcSurfaceStyle on the
    IfcMaterial, not from the source object's material slots."""
    if not blender_material or not STYLE_AVAILABLE:
        return None
    try:
        style = tool.Ifc.get_entity(blender_material)
        if not style:
            style = call_core(core_style.add_style, obj=blender_material)
        if style:
            ifcopenshell.api.run(
                "style.assign_material_style",
                ifc_file,
                material=ifc_material,
                style=style,
                context=context,
            )
        return style
    except Exception as e:
        print(f"[refops.bim] style for '{blender_material.name}': {e}")
        return None


def assign_layer_set(ifc_file, type_entity, name, layers):
    """layers: iterable of (ifc_material, thickness). Walls and slabs."""
    layer_set = ifcopenshell.api.run(
        "material.add_material_set", ifc_file, name=name, set_type="IfcMaterialLayerSet"
    )
    for material, thickness in layers:
        layer = ifcopenshell.api.run(
            "material.add_layer", ifc_file, layer_set=layer_set, material=material
        )
        ifcopenshell.api.run(
            "material.edit_layer",
            ifc_file,
            layer=layer,
            attributes={"LayerThickness": thickness},
        )
    ifcopenshell.api.run(
        "material.assign_material",
        ifc_file,
        products=[type_entity],
        type="IfcMaterialLayerSet",
        material=layer_set,
    )
    return layer_set


def layer_set_signature(entity, precision=4):
    """((thickness, material_name), ...) or None. Comparing signatures reuses a
    type made by Bonsai's own tools or by an earlier run, whatever its name."""
    material = ifcopenshell.util.element.get_material(entity)
    if not material or not material.is_a("IfcMaterialLayerSet"):
        return None
    return tuple(
        (
            round(layer.LayerThickness or 0.0, precision),
            layer.Material.Name if layer.Material else None,
        )
        for layer in (material.MaterialLayers or [])
    )


def find_type_by_layers(ifc_file, ifc_class, layers, precision=4):
    """layers: iterable of (thickness, material_name)."""
    wanted = tuple(
        (round(thickness, precision), mat_name) for thickness, mat_name in layers
    )
    for entity in ifc_file.by_type(ifc_class):
        if layer_set_signature(entity, precision) == wanted:
            return entity
    return None


# --- CLEANUP ---


def cleanup_family(ifc_file, ifc_classes, key):
    """Remove every element this container produced on a previous run. Driven by
    the IFC pset rather than by Blender objects, so entities orphaned by a
    deleted object are collected too."""
    if isinstance(ifc_classes, str):
        ifc_classes = (ifc_classes,)
    removed = 0
    for ifc_class in ifc_classes:
        for element in list(ifc_file.by_type(ifc_class)):
            psets = ifcopenshell.util.element.get_psets(element)
            if psets.get(FAMILY_PSET, {}).get("Family") != key:
                continue
            obj = tool.Ifc.get_object(element)
            try:
                ifcopenshell.api.run("root.remove_product", ifc_file, product=element)
            except Exception as e:
                print(f"[refops.bim] remove_product: {e}")
            if isinstance(obj, bpy.types.Object):
                remove_object(obj)
            removed += 1
    return removed


# Custom property carrying the family key on Blender objects that have no IFC
# entity behind them — proxies, helpers. Those can't be found through the pset
# sweep, and matching by name prefix has bitten this addon more than once.
OWNER_KEY = "refops_family"


class TypeIndexer:
    """Numbers the distinct IFC types one operation used: 0, 1, 2, ... in the
    order they are first needed.

    One instance per run, so the numbering restarts at zero for every operation
    — the panel run's 0 and the wall run's 0 are unrelated, which is the point.
    Keyed on the entity's STEP id rather than its name, so a type that gets
    renamed later doesn't split into two indices."""

    def __init__(self):
        self._by_id = {}

    def index(self, entity):
        key = entity.id() if entity is not None else -1
        if key not in self._by_id:
            self._by_id[key] = len(self._by_id)
        return self._by_id[key]

    def __len__(self):
        return len(self._by_id)


def set_type_index(obj, index, name="type"):
    """Write the type number onto the element's faces.

    Applied in the finishing pass, alongside the material slots and for the same
    reason: anything that rebuilds the mesh from the IFC representation drops
    mesh attributes, so writing this earlier would silently lose it.

    Bonsai-managed objects are ordinary Blender meshes — an attribute here is as
    real as on anything else, and reads normally in shaders and Geometry Nodes.
    It lives in the .blend, not in the IFC; a pset is the place for data that
    has to survive export."""
    mesh = getattr(obj, "data", None)
    if not mesh or not getattr(mesh, "polygons", None):
        return None
    attribute = mesh.attributes.get(name)
    if attribute is not None and (
        attribute.data_type != "INT" or attribute.domain != "FACE"
    ):
        mesh.attributes.remove(attribute)
        attribute = None
    if attribute is None:
        attribute = mesh.attributes.new(name=name, type="INT", domain="FACE")
    attribute.data.foreach_set("value", [int(index)] * len(mesh.polygons))
    mesh.update()
    return attribute


def tag_object(obj, key):
    obj[OWNER_KEY] = key


def cleanup_tagged_objects(key):
    """Remove the non-IFC objects a previous run of this container produced."""
    removed = 0
    for obj in list(bpy.data.objects):
        try:
            if obj.get(OWNER_KEY) != key:
                continue
        except ReferenceError:
            continue
        remove_object(obj)
        removed += 1
    return removed


def split_islands(faces):
    """Face groups connected through shared edges. Two disconnected shells are
    two elements, not one element holding both."""
    remaining = set(faces)
    islands = []
    while remaining:
        island = [remaining.pop()]
        stack = list(island)
        while stack:
            face = stack.pop()
            for edge in face.edges:
                for linked in edge.link_faces:
                    if linked in remaining:
                        remaining.discard(linked)
                        island.append(linked)
                        stack.append(linked)
        islands.append(island)
    return islands


def cleanup_orphan_objects(name_prefix, exclude=()):
    """Objects left behind by a run that failed after creating the object but
    before (or while) creating its entity.

    Procedural copies are never candidates. A RefOps container is itself a
    procedural copy with no IFC entity, so a prefix that happens to match its
    name would have this function delete the very object the operation is
    running on — and the data_holder lives on that object, so the next property
    read dereferences freed memory and takes Blender down with it. Matching by
    name is a blunt instrument; this is the guard that keeps it survivable."""
    excluded = {o.name for o in exclude if o}
    for obj in list(bpy.data.objects):
        if obj.name in excluded:
            continue
        if getattr(obj, "is_procedural_copy", False):
            continue
        if obj.name.startswith(name_prefix) and tool.Ifc.get_entity(obj) is None:
            remove_object(obj)


def remove_object(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh and getattr(mesh, "users", 1) == 0:
        try:
            bpy.data.meshes.remove(mesh)
        except (TypeError, ReferenceError):
            pass


# --- CONTEXTS ---


def profile_of_type(type_entity):
    """The IfcProfileDef carried by a type's IfcMaterialProfileSet, or None."""
    material = ifcopenshell.util.element.get_material(type_entity)
    if not material or not material.is_a("IfcMaterialProfileSet"):
        return None
    profiles = material.MaterialProfiles or []
    return profiles[0].Profile if profiles else None


def profile_half_extents(profile):
    """Half width and half depth of a section's bounding box.

    Needed to place a cardinal point, which is expressed relative to the
    section's extents. Unknown profile classes fall back to (0, 0) — the
    cardinal offset then does nothing and the section stays centred, which is
    wrong-but-harmless rather than a crash."""
    if profile is None:
        return 0.0, 0.0
    if profile.is_a("IfcRectangleProfileDef"):
        return (profile.XDim or 0.0) / 2.0, (profile.YDim or 0.0) / 2.0
    if profile.is_a("IfcCircleProfileDef"):
        radius = profile.Radius or 0.0
        return radius, radius
    if profile.is_a("IfcParameterizedProfileDef"):
        # I/T/U/L/Z and friends expose overall dimensions under varying names.
        width = getattr(profile, "OverallWidth", None) or getattr(profile, "Width", None)
        depth = getattr(profile, "OverallDepth", None) or getattr(profile, "Depth", None)
        if width and depth:
            return width / 2.0, depth / 2.0
    if profile.is_a("IfcArbitraryClosedProfileDef"):
        curve = profile.OuterCurve
        points = getattr(curve, "Points", None) if curve else None
        coords = [p.Coordinates for p in points] if points else []
        if coords:
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            return (max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0
    return 0.0, 0.0


def find_type_by_name(ifc_file, ifc_class, name):
    return next((t for t in ifc_file.by_type(ifc_class) if t.Name == name), None)


def extruded_representation(ifc_file, context, profile, depth, offset_2d=(0.0, 0.0)):
    """Body representation extruding any IfcProfileDef along +Z.

    offset_2d shifts the profile inside the extrusion — that is how a cardinal
    point is realised geometrically, by moving the section relative to the
    reference line rather than moving the object."""
    solid = ifc_file.createIfcExtrudedAreaSolid(
        SweptArea=profile,
        Position=ifc_file.createIfcAxis2Placement3D(
            ifc_file.createIfcCartesianPoint(
                (float(offset_2d[0]), float(offset_2d[1]), 0.0)
            ),
            None,
            None,
        ),
        ExtrudedDirection=ifc_file.createIfcDirection((0.0, 0.0, 1.0)),
        Depth=float(depth),
    )
    return ifc_file.createIfcShapeRepresentation(
        ContextOfItems=context,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )


def swept_solid_representation(ifc_file, context, points_2d, depth):
    """Body representation for a flat, arbitrary polygonal footprint extruded
    along +Z. Built from raw IFC4 entities rather than a bonsai.core helper
    because the entity signatures are stable across versions; the result is a
    SweptSolid over an IfcArbitraryClosedProfileDef, which is the same shape
    Bonsai's own tools produce.

    points_2d must be in the product's LOCAL XY plane and wound
    counter-clockwise; the ring is closed here."""
    ring = list(points_2d)
    if ring[0] != ring[-1]:
        ring.append(ring[0])

    polyline = ifc_file.createIfcPolyline(
        Points=[
            ifc_file.createIfcCartesianPoint((float(x), float(y))) for x, y in ring
        ]
    )
    profile = ifc_file.createIfcArbitraryClosedProfileDef(
        ProfileType="AREA", OuterCurve=polyline
    )
    solid = ifc_file.createIfcExtrudedAreaSolid(
        SweptArea=profile,
        Position=ifc_file.createIfcAxis2Placement3D(
            ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None
        ),
        ExtrudedDirection=ifc_file.createIfcDirection((0.0, 0.0, 1.0)),
        Depth=float(depth),
    )
    return ifc_file.createIfcShapeRepresentation(
        ContextOfItems=context,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )


def get_context(ifc_file, context_type="Model", identifier="Body",
                target_view="MODEL_VIEW"):
    """Get-or-create a representation context.

    Bonsai only creates the contexts a project actually used, so an operator
    writing a plan or section representation has to be able to mint the one it
    needs — a missing Plan/Body/PLAN_VIEW would otherwise fail at the moment the
    geometry is assigned, with nothing said about why."""
    context = ifcopenshell.util.representation.get_context(
        ifc_file, context_type, identifier, target_view
    )
    if context:
        return context
    parent = ifcopenshell.util.representation.get_context(ifc_file, context_type)
    if not parent:
        parent = ifcopenshell.api.run(
            "context.add_context", ifc_file, context_type=context_type
        )
    return ifcopenshell.api.run(
        "context.add_context",
        ifc_file,
        context_type=context_type,
        context_identifier=identifier,
        target_view=target_view,
        parent=parent,
    )


def get_body_context(ifc_file):
    return get_context(ifc_file, "Model", "Body", "MODEL_VIEW")


# --- MESH ANALYSIS ---


def group_coplanar_faces(faces, matrix_world, precision=3, extra_key=None):
    """Groups faces by world-space plane and material. extra_key(face) adds more
    fields to the grouping key — e.g. an alignment attribute, so faces meant to
    align differently don't merge into one element."""
    groups = {}
    rot = matrix_world.to_3x3()
    for f in faces:
        normal = (rot @ f.normal).normalized()
        center = matrix_world @ f.calc_center_median()
        key = (
            round(normal.x, precision),
            round(normal.y, precision),
            round(normal.z, precision),
            round(normal.dot(center), precision),
            f.material_index,
        )
        if extra_key is not None:
            key += (extra_key(f),)
        groups.setdefault(key, []).append(f)
    return list(groups.values())


def dissolve_regions(bm, faces):
    """Merges a face group and returns EVERY resulting region. Coplanar faces
    that aren't connected produce more than one region — taking only the first
    silently drops the other segments."""
    import bmesh

    if len(faces) == 1:
        return list(faces)
    try:
        regions = list(
            bmesh.ops.dissolve_faces(bm, faces=faces, use_verts=False).get("region")
            or []
        )
    except Exception as e:
        print(f"[refops.bim] dissolve_faces: {e}")
        regions = []
    if regions:
        return regions
    # Dissolve refuses when the group's boundary is not a clean loop — a slab
    # embedded in the same mesh as its walls hits this, and returning nothing
    # meant no element at all. The undissolved faces still describe the same
    # area, so each becomes its own element rather than none.
    print(
        f"[refops.bim] Dissolve falhou em {len(faces)} face(s); "
        "usando as faces originais."
    )
    return list(faces)
