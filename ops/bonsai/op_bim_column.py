import refops_api

EdgeComponent = refops_api.bim_profiles.EdgeComponent


class BonsaiColumn(EdgeComponent):
    """Bonsai IFC Column.

    Everything lives in refops_api.bim_profiles.EdgeComponent — only the IFC
    identity and the axis rule differ between the three edge-driven components,
    so this file is the whole difference.

    VERTICAL_ONLY is what makes a column a column: the height comes
    from the Z difference alone and the origin drops to the lower
    vertex, so a slanted edge still produces a plumb column."""

    id = "BIM_COLUMN"
    label = "Bonsai IFC Column"
    icon = "MESH_CYLINDER"

    IFC_CLASS = "IfcColumn"
    TYPE_CLASS = "IfcColumnType"
    PREFIX = "COL"
    VERTICAL_ONLY = True
