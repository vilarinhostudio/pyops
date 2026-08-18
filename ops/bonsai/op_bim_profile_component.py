import refops_api

EdgeComponent = refops_api.bim_profiles.EdgeComponent


class BonsaiProfileComponent(EdgeComponent):
    """Bonsai IFC Profile Component.

    Everything lives in refops_api.bim_profiles.EdgeComponent — only the IFC
    identity and the axis rule differ between the three edge-driven components,
    so this file is the whole difference.

    The one thing it does NOT share is the fixed class: a column is an
    IfcColumn and a beam an IfcBeam, but "profile component" names a shape, not
    a role, so the class is an input here. IFC_CLASS stays as the default the
    field starts from."""

    id = "BIM_PROFILE_COMPONENT"
    label = "Bonsai IFC Profile Component"
    icon = "MOD_THICKNESS"

    IFC_CLASS = "IfcMember"
    TYPE_CLASS = "IfcMemberType"
    PREFIX = "PRF"
    VERTICAL_ONLY = False

    props_definition = refops_api.bim_profiles.with_ifc_class(
        EdgeComponent.props_definition, default=IFC_CLASS
    )
