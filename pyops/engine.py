"""The operators that actually run operations: the duplication engine, the
folder reload, and the bulk refresh commands."""

import bpy

from . import profiling
from . import registry
from .loader import load_operations_from_disk
from .properties import register_data_storage, unregister_data_storage
from .registry import OPERATIONS, UI_ONLY_TYPES, make_active, op_holder
from .snapshot import restore_downstream_data, snapshot_downstream_data


def _strip_animation(obj):
    """A procedural copy has no business being animated: it is rebuilt from the
    parent on every refresh, so keyframes on it are dead weight at best and
    fight the rebuild at worst. Modifier values already evaluated for this frame
    survive — clearing the animation unlinks the Action, it does not reset the
    properties it was driving."""
    animation = getattr(obj, "animation_data", None)
    if animation is None:
        return
    actions = {animation.action} if animation.action else set()
    for track in animation.nla_tracks:
        for strip in track.strips:
            if strip.action:
                actions.add(strip.action)
    obj.animation_data_clear()
    for action in actions:
        try:
            if not action.users and not action.use_fake_user:
                bpy.data.actions.remove(action)
        except (ReferenceError, TypeError):
            pass


@bpy.app.handlers.persistent
def procedural_live_depsgraph_handler(scene, depsgraph):
    if not depsgraph.updates:
        return

    updated_names = {
        update.id.name
        for update in depsgraph.updates
        if isinstance(update.id, bpy.types.Object)
    }
    if not updated_names:
        return

    for obj in scene.objects:
        if obj.is_procedural_copy and obj.procedural_auto_update:
            parent_name = obj.proc_parent_name
            if parent_name in updated_names:
                obj_parent = bpy.data.objects.get(parent_name)
                if obj_parent:
                    ctx = bpy.context.copy()
                    ctx["active_object"] = obj
                    bpy.ops.mesh.procedural_engine(ctx, is_refresh=True)


class MESH_OT_procedural_engine(bpy.types.Operator):
    bl_idname = "mesh.procedural_engine"
    bl_label = "Run Operation"
    bl_options = {"REGISTER", "UNDO"}

    is_refresh: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        registry.refresh_lock = True
        try:
            return self._run(context)
        finally:
            registry.refresh_lock = False

    def _run(self, context):
        scn = context.scene
        active_obj = context.active_object

        if self.is_refresh:
            if not active_obj or not active_obj.is_procedural_copy:
                return {"CANCELLED"}
            parent_name = active_obj.proc_parent_name
            obj1 = bpy.data.objects.get(parent_name)
            op_id = active_obj.procedural_op_type
            data_holder = op_holder(active_obj.procedural_properties, op_id)
        else:
            obj1 = scn.ui_parent
            op_id = scn.ui_op_dropdown
            if not obj1 or not op_id or len(scn.ui_factory_props) == 0:
                return {"CANCELLED"}
            data_holder = op_holder(scn.ui_factory_props[0], op_id)

        if op_id not in OPERATIONS:
            self.report({"ERROR"}, "Operation unavailable.")
            return {"CANCELLED"}

        if not obj1:
            self.report({"ERROR"}, "Parent object not found.")
            return {"CANCELLED"}

        if data_holder is None:
            self.report({"ERROR"}, "Operation properties unavailable.")
            return {"CANCELLED"}

        op_class = OPERATIONS[op_id]
        saved_values = {}
        for prop_name, (prop_type, _) in op_class.props_definition.items():
            if prop_type in UI_ONLY_TYPES:
                continue
            value = getattr(data_holder, prop_name)
            # Vector/Color props hand back a live proxy into the owner's RNA
            # data, not a copy. The object that owns data_holder is deleted a
            # few lines below, and writing the proxy back afterwards reads freed
            # memory — an access violation, not a Python exception. Materialise
            # it now, while the owner is still alive.
            if hasattr(value, "__len__") and not isinstance(value, str):
                value = tuple(value)
            saved_values[prop_name] = value
        auto_update_state = (
            active_obj.procedural_auto_update if self.is_refresh else False
        )
        order_state = active_obj.procedural_order if self.is_refresh else 0

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        preserved_data = None
        kept_collections = None
        if self.is_refresh:
            preserved_data = snapshot_downstream_data(active_obj, obj1)
            for o in list(context.view_layer.objects):
                if (
                    o.is_procedural_copy
                    and o.proc_parent_name == obj1.name
                    and o.procedural_op_type == op_id
                ):
                    # The duplicate below is born in the parent's collections;
                    # remember where the user filed the old copy to put it back.
                    if kept_collections is None:
                        kept_collections = list(o.users_collection)
                    # Frees the mesh as well — see registry.remove_object.
                    registry.remove_object(o)

        obj1.hide_set(False)
        bpy.ops.object.select_all(action="DESELECT")
        obj1.select_set(True)
        context.view_layer.objects.active = obj1

        # Duplicating an animated parent copies its Action too, so every refresh
        # minted a CubeAction.001, .002, ... — one per frame under frame-driven
        # refresh. Turning the preference off means no copy is made in the first
        # place; the copy shares the parent's Action and is unlinked from it a
        # moment later.
        edit_prefs = bpy.context.preferences.edit
        had_duplicate_action = getattr(edit_prefs, "use_duplicate_action", None)
        if had_duplicate_action is not None:
            edit_prefs.use_duplicate_action = False
        try:
            bpy.ops.object.duplicate()
        finally:
            if had_duplicate_action is not None:
                edit_prefs.use_duplicate_action = had_duplicate_action

        nova_copia = context.active_object
        if kept_collections:
            for collection in list(nova_copia.users_collection):
                collection.objects.unlink(nova_copia)
            for collection in kept_collections:
                try:
                    collection.objects.link(nova_copia)
                except RuntimeError as e:
                    print(f"[PyOps.engine] collection '{collection.name}': {e}")
            if not nova_copia.users_collection:
                context.scene.collection.objects.link(nova_copia)
        _strip_animation(nova_copia)
        nova_copia.is_procedural_copy = True
        nova_copia.procedural_op_type = op_id
        nova_copia.proc_parent_name = obj1.name
        nova_copia.procedural_auto_update = auto_update_state
        nova_copia.procedural_order = order_state
        obj1.hide_set(True)

        # Bake down the modifiers the parent shows in the viewport, so the op runs
        # on the same mesh the user sees. A modifier toggled off stays on the copy
        # as a live modifier instead of being applied — that toggle is the user
        # saying "not part of the result", and applying it anyway would bake in
        # geometry they had deliberately hidden.
        for mod in list(nova_copia.modifiers):
            if not mod.show_viewport:
                continue
            try:
                bpy.ops.object.modifier_apply(modifier=mod.name)
            except RuntimeError as e:
                print(f"[{__package__}] Could not apply modifier {mod.name}: {e}")

        new_holder = op_holder(nova_copia.procedural_properties, op_id)
        for prop_name, value in saved_values.items():
            setattr(new_holder, prop_name, value)

        # 'EDIT' (default) = classic mesh-editing operation, runs inside Edit Mode.
        # 'OBJECT' = operates on the object itself (e.g. an Empty container that
        # walks obj.children) — Edit Mode doesn't exist for those object types.
        execution_mode = getattr(op_class, "execution_mode", "EDIT")
        with profiling.operation(op_id):
            if execution_mode == "EDIT":
                bpy.ops.object.mode_set(mode="EDIT")
                op_success = op_class.execute(nova_copia, new_holder)
                bpy.ops.object.mode_set(mode="OBJECT")
            else:
                op_success = op_class.execute(nova_copia, new_holder)

        if not op_success:
            self.report(
                {"WARNING"},
                f"'{op_class.label}' did not apply — check the console for details.",
            )

        if preserved_data:
            restore_downstream_data(nova_copia, preserved_data)

        if not self.is_refresh:
            scn.ui_factory_props.clear()
            scn.ui_parent = None
            context.view_layer.objects.active = nova_copia
            nova_copia.select_set(True)

        return {"FINISHED"}


class MESH_OT_reload_procedural_scripts(bpy.types.Operator):
    bl_idname = "mesh.reload_procedural_scripts"
    bl_label = "Reload Operator Folders"

    def execute(self, context):
        unregister_data_storage()
        load_operations_from_disk()
        register_data_storage()
        return {"FINISHED"}


class MESH_OT_procedural_refresh_all(bpy.types.Operator):
    bl_idname = "mesh.procedural_refresh_all"
    bl_label = "Refresh All"
    bl_description = "Refreshes every procedural copy in the scene"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        # Names, not objects: each refresh deletes and recreates its copy, so a
        # reference taken now would be dangling by the next iteration.
        targets = [o.name for o in registry.ordered_copies(bpy.data.objects)]
        for name in targets:
            obj = bpy.data.objects.get(name)
            if not obj or not obj.is_procedural_copy:
                continue  # may already have been recreated by an earlier refresh in this loop
            make_active(obj)
            bpy.ops.mesh.procedural_engine(is_refresh=True)
        return {"FINISHED"}


class MESH_OT_procedural_refresh_selected(bpy.types.Operator):
    bl_idname = "mesh.procedural_refresh_selected"
    bl_label = "Refresh Selected"
    bl_description = "Refreshes only the selected procedural copies"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        targets = [
            o.name for o in registry.ordered_copies(context.selected_objects)
        ]
        if not targets:
            self.report({"WARNING"}, "No procedural copies selected.")
            return {"CANCELLED"}
        for name in targets:
            obj = bpy.data.objects.get(name)
            if not obj or not obj.is_procedural_copy:
                continue
            make_active(obj)
            bpy.ops.mesh.procedural_engine(is_refresh=True)
        return {"FINISHED"}


classes = (
    MESH_OT_procedural_engine,
    MESH_OT_reload_procedural_scripts,
    MESH_OT_procedural_refresh_all,
    MESH_OT_procedural_refresh_selected,
)
