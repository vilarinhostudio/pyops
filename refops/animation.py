"""Frame-driven refresh.

Rebuilding every procedural copy on every frame is far too expensive to do
blindly — a refresh duplicates an object, bakes modifiers, reruns the operation
and, for the BIM operators, destroys and recreates IFC entities. So the handler
does two things before committing to that: it only looks at copies explicitly
opted in, and it fingerprints the source geometry and skips the rebuild when
nothing actually moved. A frame where geometry nodes produced identical output
costs one hash and nothing else.
"""

import array
import hashlib
import traceback

import bpy

from . import registry

# Attribute data types that can be read in bulk, mapped to
# (property name, array typecode, components per element). Anything else is
# skipped — it just doesn't contribute to the fingerprint.
_BULK_ATTRS = {
    "FLOAT": ("value", "f", 1),
    "INT": ("value", "i", 1),
    "INT8": ("value", "i", 1),
    "BOOLEAN": ("value", "i", 1),
    "FLOAT_VECTOR": ("vector", "f", 3),
    "FLOAT2": ("vector", "f", 2),
    "FLOAT_COLOR": ("color", "f", 4),
    "BYTE_COLOR": ("color", "f", 4),
}

_busy = False
_rendering = False
_fingerprints = {}


def fingerprint(obj, depsgraph):
    """Hash of the EVALUATED source geometry.

    Evaluated, not original: geometry nodes write to the evaluated mesh, and the
    original never changes as the frame advances. Hashing obj.data instead would
    return the same value forever and nothing would ever update."""
    try:
        evaluated = obj.evaluated_get(depsgraph) if depsgraph else obj
        mesh = evaluated.data
    except (ReferenceError, AttributeError):
        return None
    if not isinstance(mesh, bpy.types.Mesh):
        return None

    digest = hashlib.blake2b(digest_size=8)
    try:
        # Instance transforms first: a nodes tree that only instances leaves the
        # object's own mesh empty and unchanged, so hashing the mesh alone would
        # report "nothing moved" forever while the instances animate underneath.
        if depsgraph is not None:
            for instance in depsgraph.object_instances:
                if not instance.is_instance:
                    continue
                parent = instance.parent
                if parent is None or parent.original != obj:
                    continue
                digest.update(
                    array.array(
                        "f",
                        [c for row in instance.matrix_world for c in row],
                    ).tobytes()
                )

        count = len(mesh.vertices)
        coords = array.array("f", [0.0]) * (count * 3)
        mesh.vertices.foreach_get("co", coords)
        digest.update(coords.tobytes())

        for attribute in mesh.attributes:
            spec = _BULK_ATTRS.get(attribute.data_type)
            if not spec:
                continue
            field, typecode, width = spec
            size = len(attribute.data) * width
            if not size:
                continue
            buffer = array.array(typecode, [0]) * size
            attribute.data.foreach_get(field, buffer)
            digest.update(attribute.name.encode())
            digest.update(buffer.tobytes())
    except Exception:
        return None
    return digest.hexdigest()


def _find_copy(parent_name, op_id):
    """Copies are deleted and recreated by every refresh, so their names are not
    stable. The (parent, operation) pair is."""
    for obj in bpy.data.objects:
        if (
            obj.is_procedural_copy
            and obj.proc_parent_name == parent_name
            and obj.procedural_op_type == op_id
        ):
            return obj
    return None


@bpy.app.handlers.persistent
def frame_change_handler(scene, depsgraph=None):
    global _busy
    if _busy or _rendering:
        return
    if not getattr(scene, "ui_frame_refresh_enabled", False):
        return

    # Same ordering as a manual Refresh All — a frame-driven rebuild has the
    # same dependencies as a button-driven one.
    targets = [
        (obj.proc_parent_name, obj.procedural_op_type)
        for obj in registry.ordered_copies(scene.objects)
        if obj.procedural_auto_update
    ]
    if not targets:
        return

    _busy = True
    try:
        for parent_name, op_id in targets:
            parent = bpy.data.objects.get(parent_name)
            copy = _find_copy(parent_name, op_id)
            if not parent or not copy:
                continue

            stamp = fingerprint(parent, depsgraph)
            if stamp is not None and _fingerprints.get((parent_name, op_id)) == stamp:
                continue
            _fingerprints[(parent_name, op_id)] = stamp

            try:
                registry.make_active(copy)
                bpy.ops.mesh.procedural_engine(is_refresh=True)
            except Exception:
                print(f"[{__package__}] Frame refresh falhou em '{parent_name}':")
                traceback.print_exc()
    finally:
        _busy = False


@bpy.app.handlers.persistent
def render_start_handler(*args):
    """Operators are not safe to run from a frame handler while a render is in
    flight, so frame refresh stands down for the duration. Bake the geometry
    before rendering an animation."""
    global _rendering
    _rendering = True


@bpy.app.handlers.persistent
def render_end_handler(*args):
    global _rendering
    _rendering = False


def forget_fingerprints():
    _fingerprints.clear()
