import bpy, bmesh


class GP_MaterialConfig:
    id = "MATERIAL_CONFIG"
    label = "Material Config"
    icon = "MATERIAL_DATA"
    data_type = "ANY"

    props_definition = {
        # 
        }

    @classmethod
    def execute(cls, obj, data_holder):
        return True
