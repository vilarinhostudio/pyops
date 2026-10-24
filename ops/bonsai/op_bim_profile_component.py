
EdgeComponent = pyops_api.bim_profiles.EdgeComponent


def with_ifc_class(props, default):
    """The shared edge-component props plus a free IFC Class field, inserted
    under the BIM Properties header so it reads next to the storey.

    Lives here rather than in bim_profiles because this is the only component
    whose class is an input."""
    out = {}
    for key, value in props.items():
        out[key] = value
        if key == "lbl_bim":
            out["ifc_class"] = ("String", {"name": "IFC Class", "default": default})
    return out


class BonsaiProfileComponent(EdgeComponent):
    """Bonsai IFC Profile Component.

    Everything lives in pyops_api.bim_profiles.EdgeComponent — only the IFC
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

    props_definition = with_ifc_class(EdgeComponent.props_definition, IFC_CLASS)
