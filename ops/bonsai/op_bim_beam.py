
EdgeComponent = pyops_api.bim_profiles.EdgeComponent


class BonsaiBeam(EdgeComponent):
    """Bonsai IFC Beam.

    Everything lives in pyops_api.bim_profiles.EdgeComponent — only the IFC
    identity and the axis rule differ between the three edge-driven components,
    so this file is the whole difference."""

    id = "BIM_BEAM"
    label = "Bonsai IFC Beam"
    icon = "MESH_CUBE"

    IFC_CLASS = "IfcBeam"
    TYPE_CLASS = "IfcBeamType"
    PREFIX = "BEA"
    VERTICAL_ONLY = False
