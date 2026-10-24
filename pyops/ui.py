"""N-panel drawing and addon preferences."""

import bpy

from . import dependencies
from . import registry as registry_module
from . import search
from .callbacks import NO_SELECTION
from .attributes import field_for, spec_for
from .properties import resolve_alias
from .registry import OPERATIONS, op_holder

registry = registry_module


def is_visible(data_holder, show_if):
    """A field is drawn when every condition in show_if holds.

    One condition is a (field, values) tuple; a list of tuples is ANDed. The
    single-tuple form is what most operators use, but a chart whose sub-type
    enum narrows an already-narrowed set needs both — without the AND, a
    monthly-only field shows up under the hourly chart too."""
    if not show_if:
        return True
    conditions = show_if if isinstance(show_if, list) else [show_if]
    for field, values in conditions:
        if getattr(data_holder, field, None) not in values:
            return False
    return True


def draw_op_properties(layout, op_cls, data_holder, context):
    """Draws an operator's props_definition in order. Real props bind to data_holder;
    Label/Separator/Space are pure layout, no property involved.

    Any real field can add "show_if": ("controlling_prop_name", {"value1", "value2"})
    to its args — it's only drawn while the named field currently holds one of
    those values. The property itself still exists and keeps its value even while
    hidden; only the drawing is conditional."""
    if data_holder is None:
        layout.label(text="Properties unavailable", icon="ERROR")
        return

    parent = None
    for prop_name, (prop_type, prop_args) in op_cls.props_definition.items():
        if not is_visible(data_holder, prop_args.get("show_if")):
            continue

        if prop_type == "Label":
            layout.label(
                text=prop_args.get("text", prop_name),
                icon=prop_args.get("icon", "NONE"),
            )
            continue
        if prop_type == "Separator":
            layout.separator(type="LINE")
            continue
        if prop_type == "Space":
            layout.separator(type="SPACE")
            continue

        label = prop_args.get("name", prop_name)
        base_type, args = resolve_alias(prop_type, prop_args)
        source = args.get("search") or (
            "attribute" if args.get("attribute_search") else None
        )

        if base_type == "String" and source in search.SEARCH_SOURCES:
            if parent is None:
                parent = search.resolve_search_parent(data_holder, context)
            target = search.resolve_search_target(source, parent, context)
            if target is not None:
                data, collection, icon = target
                layout.prop_search(
                    data_holder, prop_name, data, collection, text=label, icon=icon
                )
            else:
                # No parent picked yet (or it has no mesh) — fall back to a plain
                # text field rather than hiding the row entirely.
                layout.prop(data_holder, prop_name, text=label)
            continue

        # Multi-component props get the same treatment as the attribute panel:
        # a label with the components stacked underneath. Left to itself,
        # layout.prop squeezes them into one cramped row.
        try:
            components = len(getattr(data_holder, prop_name))
        except TypeError:
            components = 1
        if components > 1 and base_type in {"Vector", "IntVector", "Color"}:
            layout.label(text=label)
            layout.column(align=True).prop(data_holder, prop_name, text="")
            continue

        layout.prop(data_holder, prop_name, text=label)


def draw_attribute_row(layout, row):
    spec = spec_for(row.data_type)
    if not spec:
        return
    _, kind, components = spec

    if not row.editable:
        split = layout.split(factor=0.45)
        split.label(text=row.attr_name)
        sub = split.row()
        sub.enabled = False
        sub.label(text=f"{row.data_type} — no bmesh access")
        return

    if kind == "matrix":
        layout.label(text=f"{row.attr_name}  (4x4)")
        grid = layout.column(align=True)
        for r in range(4):
            line = grid.row(align=True)
            for c in range(4):
                line.prop(row, "matrix_value", index=r * 4 + c, text="")
        return

    field = field_for(row)
    if not field:
        return

    if kind == "color":
        split = layout.split(factor=0.45)
        split.label(text=row.attr_name)
        split.prop(row, field, text="")
        return

    if kind in {"vector", "int_vector"}:
        layout.label(text=row.attr_name)
        layout.column(align=True).prop(row, field, text="")
        return

    split = layout.split(factor=0.45)
    split.label(text=row.attr_name)
    split.prop(row, field, text="")


class VIEW3D_PT_procedural_attributes(bpy.types.Panel):
    bl_label = "Selection Attributes"
    bl_idname = "VIEW3D_PT_procedural_attributes"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Procedural"
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and context.edit_object is not None

    def draw(self, context):
        layout = self.layout
        obj = context.edit_object
        if not obj or obj.type != "MESH":
            return

        rows = context.scene.ui_attr_rows
        if not rows:
            layout.label(text="No editable attributes on selection.", icon="INFO")
            return

        seen_domains = set()
        for row in rows:
            if row.domain not in seen_domains:
                seen_domains.add(row.domain)
                box = layout.box()
                label = row.domain.title() if row.domain != "POINT" else "Vertex"
                box.label(text=f"{label} · selection")

            draw_attribute_row(box, row)

        if not seen_domains:
            layout.label(text="No editable attributes on selection.", icon="INFO")


class VIEW3D_PT_procedural_sidebar(bpy.types.Panel):
    bl_label = "Procedural Operations Pipeline"
    bl_idname = "VIEW3D_PT_procedural_sidebar"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Procedural"

    def draw(self, context):
        layout = self.layout
        scn = context.scene
        active_obj = context.active_object

        layout.operator("mesh.reload_procedural_scripts", icon="FILE_REFRESH")
        layout.separator()

        box_add = layout.box()
        box_add.label(text="Add Operation", icon="MODIFIER_DATA")
        box_add.prop(scn, "ui_parent", text="Parent Object")

        if scn.ui_parent:
            box_add.prop(scn, "ui_category_dropdown", text="Category")
            if scn.ui_category_dropdown != NO_SELECTION:
                box_add.prop(scn, "ui_op_dropdown", text="Operation")
            props = getattr(scn, "ui_factory_props", None)
            if scn.ui_op_dropdown in OPERATIONS and props and len(props) > 0:
                box_add.separator()
                box_add.label(text="Initial Settings:", icon="PREFERENCES")
                draw_op_properties(
                    box_add,
                    OPERATIONS[scn.ui_op_dropdown],
                    op_holder(props[0], scn.ui_op_dropdown),
                    context,
                )
                box_add.separator()
                box_add.operator(
                    "mesh.procedural_engine",
                    text="Execute and Create Object",
                    icon="PLAY",
                ).is_refresh = False

        if active_obj and active_obj.is_procedural_copy:
            op_id = active_obj.procedural_op_type
            if op_id in OPERATIONS:
                layout.separator()
                box_edit = layout.box()
                box_edit.label(
                    text=f"Active Properties: {OPERATIONS[op_id].label}",
                    icon="MODIFIER",
                )

                box_edit.prop_search(
                    active_obj,
                    "proc_parent_name",
                    bpy.data,
                    "objects",
                    text="Parent Object",
                )
                box_edit.separator()

                draw_op_properties(
                    box_edit,
                    OPERATIONS[op_id],
                    op_holder(active_obj.procedural_properties, op_id),
                    context,
                )

                box_edit.separator()

                box_edit.prop(active_obj, "procedural_order", text="Refresh Order")

                # Per-object opt-in for the frame handler: animating everything
                # in the scene is rarely what you want.
                box_edit.prop(
                    active_obj, "procedural_auto_update", text="Update on Frame Change"
                )

                row = box_edit.row(align=True)
                refresh_btn = row.operator(
                    "mesh.procedural_engine", text="Refresh", icon="FILE_REFRESH"
                )
                refresh_btn.is_refresh = True


class VIEW3D_PT_pyops_commands(bpy.types.Panel):
    bl_label = "PyOps"
    bl_idname = "VIEW3D_PT_pyops_commands"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Procedural"
    bl_order = 1  # draws below VIEW3D_PT_procedural_sidebar

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("mesh.procedural_refresh_all", icon="FILE_REFRESH")
        col.operator("mesh.procedural_refresh_selected", icon="FILE_REFRESH")

        # The execution order, laid out — collapsed by default, since it grows
        # with the scene and is only consulted when something runs out of turn.
        copies = registry.ordered_copies(context.scene.objects)
        if copies:
            box = layout.box()
            header = box.row()
            header.prop(
                context.scene,
                "ui_show_refresh_order",
                icon="TRIA_DOWN"
                if context.scene.ui_show_refresh_order
                else "TRIA_RIGHT",
                text=f"Refresh order ({len(copies)})",
                emboss=False,
            )
            if not context.scene.ui_show_refresh_order:
                return
            for copy in copies:
                row = box.row(align=True)
                row.label(text=f"{registry.chain_depth(copy)}·")
                row.prop(copy, "procedural_order", text="")
                row.label(text=copy.name)

        try:
            prefs = context.preferences.addons[__package__].preferences
            if prefs.profile_operations:
                row = layout.row(align=True)
                row.operator("pyops.profile_report", icon="CONSOLE")
                row.operator("pyops.profile_reset", text="", icon="TRASH")
        except Exception:
            pass

        layout.separator()
        layout.prop(
            context.scene, "ui_live_refresh_enabled", text="Live Refresh on Edit"
        )
        layout.prop(
            context.scene, "ui_frame_refresh_enabled", text="Refresh on Frame Change"
        )

        # The per-copy opt-in used to live only in Active Properties, which is
        # hidden unless the copy itself is selected — so the scene toggle looked
        # on while nothing was actually enrolled. Listing them here makes the
        # empty case visible.
        if context.scene.ui_frame_refresh_enabled:
            box = layout.box()
            copies = [o for o in context.scene.objects if o.is_procedural_copy]
            if not copies:
                box.label(text="No procedural copies in the scene.", icon="INFO")
            else:
                box.label(text="Update on frame change:")
                for copy in copies:
                    row = box.row(align=True)
                    row.prop(copy, "procedural_auto_update", text="")
                    row.label(text=copy.name)

        row = layout.row()
        row.enabled = False
        row.prop(
            context.scene,
            "ui_auto_refresh_enabled",
            text="Auto-Refresh (soon)",
            toggle=True,
        )


class ProceduralAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    scripts_directory: bpy.props.StringProperty(
        name="Root Directory", subtype="DIR_PATH", default=""
    )
    show_hidden_attributes: bpy.props.BoolProperty(
        name="Show Hidden Attributes",
        description=(
            "List attributes whose name starts with a dot, like .position. "
            "Blender treats these as internal"
        ),
        default=False,
    )
    profile_operations: bpy.props.BoolProperty(
        name="Time Operations",
        description=(
            "Print how long each operation takes, and the slowest steps inside "
            "it, to the system console"
        ),
        default=False,
    )
    show_libraries: bpy.props.BoolProperty(name="Analysis Libraries", default=False)
    external_libs: bpy.props.StringProperty(
        name="Library Folder",
        subtype="DIR_PATH",
        default="C:\\pyops_libs\\",
        description=(
            "Folder for Ladybug and Honeybee. Pick one you own — Blender's own "
            "site-packages often refuses writes"
        ),
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "scripts_directory")
        layout.prop(self, "show_hidden_attributes")
        layout.prop(self, "profile_operations")

        box = layout.box()
        header = box.row()
        header.prop(
            self,
            "show_libraries",
            icon="TRIA_DOWN" if self.show_libraries else "TRIA_RIGHT",
            emboss=False,
        )
        if not self.show_libraries:
            return
        box.prop(self, "external_libs")
        if not self.external_libs:
            box.label(
                text="Set a folder outside Program Files, e.g. C:\\pyops_libs",
                icon="INFO",
            )
            return

        # This folder goes to the front of sys.path, so a complete install here
        # wins over a broken one elsewhere without deleting anything.
        status = dependencies.report()
        for group, (label, _packages) in dependencies.GROUPS.items():
            row = box.row(align=True)
            row.label(text=label)
            row.operator(
                "pyops.install_dependencies", text="Install", icon="IMPORT"
            ).group = group

        grid = box.grid_flow(columns=2, even_columns=True)
        for name, ok in status.items():
            grid.label(
                text=name,
                icon="CHECKMARK" if ok else "X",
            )
        box.operator("pyops.check_dependencies", icon="CONSOLE")
        box.label(text="Restart Blender after installing.", icon="INFO")


classes = (
    VIEW3D_PT_procedural_attributes,
    VIEW3D_PT_procedural_sidebar,
    VIEW3D_PT_pyops_commands,
    ProceduralAddonPreferences,
)
