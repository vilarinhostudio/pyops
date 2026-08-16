import bpy, bmesh

class ProceduralBevel:
    id = "BEVEL"
    label = "Bevel Edges"
    icon = 'MOD_BEVEL'
    data_type = 'MESH'

    props_definition = {
        "proc_attr": ("String", {"name": "Attribute", "search": "attribute"}),
        "sep1": ("Separator", {}),
        "bevel_offset": ("Float", {"name": "Offset", "default": 0.2, "min": 0.0}),
        "bevel_segments": ("Int", {"name": "Segments", "default": 3, "min": 1}),
    }

    @classmethod
    def execute(cls, obj, data_holder):
        # Search-enabled fields can't carry a static "default" (Blender rejects it
        # on a dynamic-items Enum), so the fallback name lives here instead.
        attr_name = data_holder.proc_attr or "b_sel"

        bm = bmesh.from_edit_mesh(obj.data)
        layer = bm.edges.layers.bool.get(attr_name)
        if not layer:
            return False
        for e in bm.edges:
            e.select = e[layer]
        bmesh.update_edit_mesh(obj.data)
        bpy.ops.mesh.bevel(offset=data_holder.bevel_offset, segments=data_holder.bevel_segments, affect='EDGES')
        return True
