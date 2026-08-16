import refops_api

EdgeComponent = refops_api.bim_profiles.EdgeComponent


class BonsaiProfileComponent(EdgeComponent):
    """Bonsai IFC Profile Component.

    Everything lives in refops_api.bim_profiles.EdgeComponent — only the IFC
    identity and the axis rule differ between the three edge-driven components,
    so this file is the whole difference."""

    id = "BIM_PROFILE_COMPONENT"
    label = "Bonsai IFC Profile Component"
    icon = "MOD_THICKNESS"

    IFC_CLASS = "IfcMember"
    TYPE_CLASS = "IfcMemberType"
    PREFIX = "PRF"
    VERTICAL_ONLY = False
