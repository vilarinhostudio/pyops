import bpy, bmesh

class ProceduralExtrude:
    id = "EXTRUDE"
    label = "Extrude Face"
    icon = 'EXPORT'
    data_type = 'MESH'

    props_definition = {
        "proc_attr": ("String", {"name": "Attribute", "search": "attribute"}),
        "extrude_value": ("Float", {"name": "Distance", "default": 0.5}),
    }

    @classmethod
    def execute(cls, obj, data_holder):
        attr_name = data_holder.proc_attr or "f_sel"

        bm = bmesh.from_edit_mesh(obj.data)
        layer = bm.faces.layers.bool.get(attr_name)
        if not layer:
            return False
        for f in bm.faces:
            f.select = f[layer]
        bmesh.update_edit_mesh(obj.data)
        bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={"value": (0, 0, data_holder.extrude_value)})
        return True
