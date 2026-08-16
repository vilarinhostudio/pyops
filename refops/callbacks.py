"""Enum item callbacks and dropdown cascade for the Add Operation panel.

Two rules govern everything here. An items callback must never raise nor return
an empty list — Blender turns both into an empty enum, which then rejects every
assignment. And Python must keep a live reference to the strings it returns, or
the enum ends up pointing at freed memory.
"""

import traceback

from . import registry
from .registry import OPERATIONS, UI_ONLY_TYPES, accepts_object_type, op_holder


def _cache_items(key, items):
    registry.enum_items_cache[key] = items
    return items


def get_categories_callback(self, context):
    """First-level dropdown: distinct category_tag values (subfolder names) that
    have at least one operation applicable to the selected parent object's type."""
    items = [("", "Select a Category...", "")]
    try:
        scn = getattr(context, "scene", None)
        obj = getattr(scn, "ui_parent", None)
        seen = set()
        if obj:
            for op_cls in OPERATIONS.values():
                tag = getattr(op_cls, "category_tag", None)
                if not tag or tag in seen or not accepts_object_type(op_cls, obj.type):
                    continue
                seen.add(tag)
                items.append((tag, tag, ""))
    except Exception:
        traceback.print_exc()
    return _cache_items("categories", items)


def get_operations_callback(self, context):
    """Second-level dropdown: operations within the selected category, applicable
    to the selected parent object's type."""
    items = [("", "Select an Operation...", "")]
    try:
        scn = getattr(context, "scene", None)
        obj = getattr(scn, "ui_parent", None)
        category = getattr(scn, "ui_category_dropdown", "")
        if obj and category:
            for op_id, op_cls in OPERATIONS.items():
                if getattr(op_cls, "category_tag", None) != category:
                    continue
                if accepts_object_type(op_cls, obj.type):
                    items.append((op_id, op_cls.label, "", op_cls.icon, len(items)))
    except Exception:
        traceback.print_exc()
    return _cache_items("operations", items)


def _reset_enum(owner, prop_name):
    """Dynamic enums store an int index; assigning an identifier is validated
    against whatever the items callback returns at that instant, which can be
    empty. Writing the raw index through the IDProperty layer never fails."""
    try:
        owner[prop_name] = 0
    except Exception:
        try:
            owner.property_unset(prop_name)
        except Exception:
            pass


def update_parent_dropdown(self, context):
    scn = context.scene
    _reset_enum(scn, "ui_category_dropdown")
    update_category_dropdown(self, context)


def update_category_dropdown(self, context):
    scn = context.scene
    _reset_enum(scn, "ui_op_dropdown")
    props = getattr(scn, "ui_factory_props", None)
    if props is not None:
        props.clear()


def update_op_dropdown(self, context):
    scn = context.scene
    props = getattr(scn, "ui_factory_props", None)
    if props is None:
        return
    props.clear()
    op_id = scn.ui_op_dropdown
    if op_id not in OPERATIONS:
        return
    holder = op_holder(props.add(), op_id)
    if holder is None:
        return
    for prop_name, (prop_type, prop_args) in OPERATIONS[op_id].props_definition.items():
        if prop_type in UI_ONLY_TYPES or "default" not in prop_args:
            continue
        try:
            setattr(holder, prop_name, prop_args["default"])
        except Exception:
            print(f"[{__package__}] Bad default for '{op_id}.{prop_name}'")
