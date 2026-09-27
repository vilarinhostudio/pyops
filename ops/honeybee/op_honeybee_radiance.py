"""Daylight analysis through Honeybee-Radiance, written onto Blender faces.

The Honeybee model comes either from the real Bonsai elements flagged Room=True
in a pset (walls, slabs, roofs, with their voids already cut) or from face
attributes painted on the parent. A recipe runs, and the numbers come back as
FLOAT attributes on the sensor geometry:

    elements | face attributes -> Honeybee model -> Radiance -> attributes

Nothing is left behind in the scene — the model and the sensor grid exist only
for the duration of the run.

This needs Radiance itself, not just the Python packages. Recipes shell out to
the binaries, so a run can take minutes and will fail in a controlled way when
they are absent.
"""

import os
import tempfile

import bpy

import refops_api

hb = refops_api.honeybee_helpers
lb = refops_api.ladybug_helpers
bim = refops_api.bim_helpers

# Recipe name and the outputs each one produces. Adding an analysis is a row
# here plus its metric names — the rest of the operator does not change.
# Recipe name, then each attribute with the output name to ask the recipe for
# and the folders to read directly if that fails. The folder list is what
# actually works: the handlers validate the layout and reject it when a level
# differs, while the files themselves are always one value per line.
RECIPES = {
    "POINT_IN_TIME_ILLUMINANCE": (
        "point-in-time-grid",
        {"illuminance": ("results", ("results/pit", "results"))},
    ),
    # ASE is deliberately absent: annual-daylight does not produce it. Its
    # outputs are da, cda, udi and the two UDI bounds, and asking for a name the
    # recipe never declares is why "ase" came back as not found.
    "ANNUAL_DAYLIGHT": (
        "annual-daylight",
        {
            "da": ("da", ("metrics/da", "annual_daylight/metrics/da")),
            "cda": ("cda", ("metrics/cda", "annual_daylight/metrics/cda")),
            "udi": ("udi", ("metrics/udi", "annual_daylight/metrics/udi")),
            "udi_lower": (
                "udi-lower",
                ("metrics/udi_lower", "annual_daylight/metrics/udi_lower"),
            ),
            "udi_upper": (
                "udi-upper",
                ("metrics/udi_upper", "annual_daylight/metrics/udi_upper"),
            ),
        },
    ),
    "ANNUAL_IRRADIANCE": (
        "annual-irradiance",
        {
            "irradiance": (
                "average-irradiance",
                ("metrics/average_irradiance", "results/average_irradiance"),
            )
        },
    ),
    "DAYLIGHT_FACTOR": (
        "daylight-factor",
        {"daylight_factor": ("results", ("results",))},
    ),
}


class HoneybeeRadiance:
    id = "HONEYBEE_RADIANCE"
    label = "Honeybee Radiance"
    icon = "OUTLINER_OB_LIGHT"
    data_type = "MESH"
    execution_mode = "OBJECT"

    props_definition = {
        "analysis_type": (
            "Enum",
            {
                "name": "Analysis",
                "items": [
                    ("POINT_IN_TIME_ILLUMINANCE", "Point-in-Time Illuminance", "lux at one moment"),
                    ("ANNUAL_DAYLIGHT", "Annual Daylight", "DA, cDA and the UDI bands"),
                    ("ANNUAL_IRRADIANCE", "Annual Irradiance", "Average irradiance"),
                    ("DAYLIGHT_FACTOR", "Daylight Factor", "Under a CIE overcast sky"),
                ],
                "default": "DAYLIGHT_FACTOR",
            },
        ),
        "sep1": ("Separator", {}),
        "lbl_model": ("Label", {"text": "Model", "icon": "MESH_DATA"}),
        "enum_model": (
            "Enum",
            {
                "name": "Build From",
                "items": [
                    ("CONSTRUCTION", "Construction", "Real walls, slabs and roofs, voids already cut"),
                    ("ATTRIBUTES", "Face Attributes", "Faces painted on the reference mesh"),
                ],
                "default": "CONSTRUCTION",
            },
        ),
        "room_name": (
            "String",
            {
                "name": "Room",
                "description": "Boolean property looked up by this name in each Room Pset below — not a Collection, so one element can serve more than one room",
                "show_if": ("enum_model", {"CONSTRUCTION"}),
            },
        ),
        "construction_pset": (
            "String",
            {
                "name": "Construction Pset",
                "default": "PyOps_RoomConstruction",
                "description": "Walls, slabs and roofs with Room=True in this pset; each mesh is used as it is, voids included",
                "show_if": ("enum_model", {"CONSTRUCTION"}),
            },
        ),
        "openings_pset": (
            "String",
            {
                "name": "Openings Pset",
                "default": "PyOps_RoomOpenings",
                "description": "Windows and doors with Room=True in this pset; matched to the voids by position",
                "show_if": ("enum_model", {"CONSTRUCTION"}),
            },
        ),
        "glazing_pset": (
            "String",
            {
                "name": "Glazing Pset",
                "default": "PyOps_Glazing",
                "description": "Pset on each opening with VisibleTransmittance and Reflectance",
                "show_if": ("enum_model", {"CONSTRUCTION"}),
            },
        ),
        "wall_attribute": ("String", {"name": "Walls", "search": "attribute", "show_if": ("enum_model", {"ATTRIBUTES"})}),
        "floor_attribute": ("String", {"name": "Floors", "search": "attribute", "show_if": ("enum_model", {"ATTRIBUTES"})}),
        "roof_attribute": ("String", {"name": "Roofs", "search": "attribute", "show_if": ("enum_model", {"ATTRIBUTES"})}),
        "opening_attribute": ("String", {"name": "Apertures", "search": "attribute", "show_if": ("enum_model", {"ATTRIBUTES"})}),
        "door_attribute": ("String", {"name": "Doors", "search": "attribute", "show_if": ("enum_model", {"ATTRIBUTES"})}),
        "sep2": ("Separator", {}),
        "lbl_context": ("Label", {"text": "Context", "icon": "OUTLINER_COLLECTION"}),
        "context_mode": (
            "Enum",
            {
                "name": "Context",
                "items": [
                    ("NONE", "None", "Only the parent object obstructs"),
                    ("COLLECTION", "Collection", "Add a collection as shading"),
                ],
                "default": "NONE",
            },
        ),
        "context": (
            "Collection",
            {"name": "Context Collection", "show_if": ("context_mode", {"COLLECTION"})},
        ),
        "sep_optics": ("Separator", {"show_if": ("enum_model", {"CONSTRUCTION"})}),
        "lbl_optics": (
            "Label",
            {"text": "Optical Properties", "icon": "MATERIAL",
             "show_if": ("enum_model", {"CONSTRUCTION"})},
        ),
        "use_optics": (
            "Bool",
            {
                "name": "Read from IFC",
                "default": True,
                "description": (
                    "Element pset, then material properties, then the display "
                    "style. Off means Radiance defaults"
                ),
                "show_if": ("enum_model", {"CONSTRUCTION"}),
            },
        ),
        "layer_side": (
            "Enum",
            {
                "name": "Finish Layer",
                "items": [
                    ("OUTER", "First Layer", "The first layer of the set"),
                    ("INNER", "Last Layer", "The last layer of the set"),
                ],
                "default": "OUTER",
                "description": "Which layer of a multi-layer wall light meets",
                "show_if": [("enum_model", {"CONSTRUCTION"}), ("use_optics", {True})],
            },
        ),
        "reflectance_pset": (
            "String",
            {
                "name": "Reflectance Pset.Prop",
                "default": "",
                "description": "Override, e.g. Pset_MaterialOptical.VisibleReflectanceFront",
                "show_if": [("enum_model", {"CONSTRUCTION"}), ("use_optics", {True})],
            },
        ),
        "transmittance_pset": (
            "String",
            {
                "name": "Transmittance Pset.Prop",
                "default": "",
                "show_if": [("enum_model", {"CONSTRUCTION"}), ("use_optics", {True})],
            },
        ),
        "specularity_pset": (
            "String",
            {
                "name": "Specularity Pset.Prop",
                "default": "",
                "show_if": [("enum_model", {"CONSTRUCTION"}), ("use_optics", {True})],
            },
        ),
        "roughness_pset": (
            "String",
            {
                "name": "Roughness Pset.Prop",
                "default": "",
                "show_if": [("enum_model", {"CONSTRUCTION"}), ("use_optics", {True})],
            },
        ),
        "report_optics": (
            "Bool",
            {
                "name": "Report Sources",
                "default": True,
                "description": "Print where each number came from — a study run on defaults looks identical to one that wasn't",
                "show_if": [("enum_model", {"CONSTRUCTION"}), ("use_optics", {True})],
            },
        ),
        "sep_lights": ("Separator", {"show_if": ("enum_model", {"CONSTRUCTION"})}),
        "lbl_lights": (
            "Label",
            {"text": "Artificial Light", "icon": "LIGHT",
             "show_if": ("enum_model", {"CONSTRUCTION"})},
        ),
        "enum_lights": (
            "Enum",
            {
                "name": "Sources",
                "items": [
                    ("NONE", "None", "Daylight only"),
                    ("ROOM_PSET", "Room Pset", "Fixtures with Room=True in a pset, flux from another pset"),
                ],
                "default": "NONE",
                "show_if": ("enum_model", {"CONSTRUCTION"}),
            },
        ),
        "light_room_pset": (
            "String",
            {
                "name": "Lights Pset",
                "default": "PyOps_RoomLights",
                "description": "Fixtures with Room=True in this pset; emitter is each fixture's largest face",
                "show_if": [("enum_model", {"CONSTRUCTION"}), ("enum_lights", {"ROOM_PSET"})],
            },
        ),
        "light_pset": (
            "String",
            {
                "name": "Light Pset",
                "default": "PyOps_Light",
                "description": "LuminousFlux (lm), or Wattage (W) at 90 lm/W",
                "show_if": [("enum_model", {"CONSTRUCTION"}), ("enum_lights", {"ROOM_PSET"})],
            },
        ),
        "sep3": ("Separator", {}),
        "lbl_sky": ("Label", {"text": "Sky", "icon": "LIGHT_SUN"}),
        "epw": (
            "Path",
            {
                "name": "EPW File",
                "path_type": "FILE",
                # Daylight factor uses a standard overcast sky and needs no
                # weather file at all.
                "show_if": (
                    "analysis_type",
                    {"POINT_IN_TIME_ILLUMINANCE", "ANNUAL_DAYLIGHT", "ANNUAL_IRRADIANCE"},
                ),
            },
        ),
        "month": ("Int", {"name": "Month", "default": 6, "min": 1, "max": 12,
                          "show_if": ("analysis_type", {"POINT_IN_TIME_ILLUMINANCE"})}),
        "day": ("Int", {"name": "Day", "default": 21, "min": 1, "max": 31,
                        "show_if": ("analysis_type", {"POINT_IN_TIME_ILLUMINANCE"})}),
        "hour": ("Float", {"name": "Hour", "default": 12.0, "min": 0.0, "max": 23.99,
                           "show_if": ("analysis_type", {"POINT_IN_TIME_ILLUMINANCE"})}),
        "sep4": ("Separator", {}),
        "lbl_result": ("Label", {"text": "Result Geometry", "icon": "FACESEL"}),
        "result_geometry_mode": (
            "Enum",
            {
                "name": "Write To",
                "items": [
                    ("SELF", "This Object", "Results land on the analysed object"),
                    ("OBJECT", "Other Object", "Results land on a sampling object"),
                ],
                "default": "SELF",
            },
        ),
        "result_face_mode": (
            "Enum",
            {
                "name": "Faces",
                "items": [
                    ("SELECTED_FACES", "Selected Faces", "Only faces flagged by the attribute"),
                    ("ALL_FACES", "All Faces", "Every face of the object"),
                ],
                "default": "SELECTED_FACES",
                "show_if": ("result_geometry_mode", {"SELF"}),
            },
        ),
        "analysis_face_attribute": (
            "String",
            {
                "name": "Face Selector (bool)",
                "search": "attribute",
                "show_if": [("result_geometry_mode", {"SELF"}),
                            ("result_face_mode", {"SELECTED_FACES"})],
            },
        ),
        "result_geometry": (
            "Object",
            {"name": "Result Object", "show_if": ("result_geometry_mode", {"OBJECT"})},
        ),
        "result_domain": (
            "Enum",
            {
                "name": "Domain",
                "items": [
                    ("FACE", "Face", "One value per face"),
                    ("VERTEX", "Vertex", "One value per vertex"),
                    ("VERTEX_GROUP", "Vertex Group", "Values as weights in a group"),
                ],
                "default": "FACE",
                "show_if": ("result_geometry_mode", {"OBJECT"}),
            },
        ),
        "output_attribute": (
            "String",
            {
                "name": "Output Attribute",
                "default": "daylight_factor",
                "description": "Annual Daylight ignores this: it writes da, cda and the udi attributes",
                "show_if": (
                    "analysis_type",
                    {"POINT_IN_TIME_ILLUMINANCE", "ANNUAL_IRRADIANCE", "DAYLIGHT_FACTOR"},
                ),
            },
        ),
        "display_material": ("Material", {"name": "Display Material"}),
        "sep5": ("Separator", {}),
        "grid_offset": (
            "Distance",
            {
                "name": "Sensor Offset",
                "default": 0.05,
                "min": 0.0,
                "description": "Sensors are lifted off their surface by this much",
            },
        ),
        "workers": (
            "Int",
            {"name": "CPU Workers", "default": 0, "min": 0,
             "description": "0 lets the recipe decide"},
        ),
    }

    # --- ENTRY POINT ---

    @classmethod
    def execute(cls, container_obj, data_holder):
        if not hb.ensure():
            print("[RefOps.honeybee] Honeybee indisponível:")
            print(hb.IMPORT_ERROR)
            return False
        if hb.radiance_path() is None:
            print(
                "[RefOps.honeybee] Radiance não encontrado. Os pacotes Python "
                "não bastam — instale o Radiance e garanta que rtrace esteja no PATH."
            )
            return False

        parent_obj = bpy.data.objects.get(getattr(container_obj, "proc_parent_name", ""))
        if not parent_obj or parent_obj.type != "MESH":
            return False

        # The container is the parent with modifiers baked, and is what the rest
        # of the chain reads — the model and the results both come from it.
        source = container_obj
        writes_elsewhere = data_holder.result_geometry_mode == "OBJECT"
        # When the results land on another object the container is just the
        # engine's duplicate of the parent sitting in the scene — the "cloned
        # house" with no colours. It only earns a solid display when it is
        # itself the thing being written to.
        source.display_type = "WIRE" if writes_elsewhere else "TEXTURED"
        source.hide_render = writes_elsewhere

        target, domain, sensors, indices = cls._resolve_target(source, data_holder)
        if not sensors:
            return False

        if data_holder.enum_model == "CONSTRUCTION":
            # Membership by Room pset, not Collection: a shared wall can be
            # True for two rooms at once, so its object can land in both
            # runs' lists without ever being moved between collections.
            construction_objects = bim.room_members(
                data_holder.construction_pset, data_holder.room_name
            )
            opening_objects = bim.room_members(
                data_holder.openings_pset, data_holder.room_name
            )
            light_objects = (
                bim.room_members(data_holder.light_room_pset, data_holder.room_name)
                if data_holder.enum_lights == "ROOM_PSET"
                else []
            )
            # No fallback to attributes: their fields are hidden in this mode,
            # so falling back would only fail again with a less useful message.
            model = hb.model_from_construction(
                construction_objects,
                openings=opening_objects,
                context_collection=(
                    data_holder.context
                    if data_holder.context_mode == "COLLECTION"
                    else None
                ),
                options={
                    "use_optics": data_holder.use_optics,
                    "outermost": data_holder.layer_side == "OUTER",
                    "report": data_holder.report_optics,
                    "glazing_pset": data_holder.glazing_pset,
                    "lights": data_holder.enum_lights,
                    "light_objects": light_objects,
                    "light_pset": data_holder.light_pset,
                    "overrides": {
                        "reflectance": data_holder.reflectance_pset,
                        "transmittance": data_holder.transmittance_pset,
                        "specularity": data_holder.specularity_pset,
                        "roughness": data_holder.roughness_pset,
                    },
                },
            )
        else:
            model = hb.model_from_object(
            source,
            {
                "WALL": data_holder.wall_attribute,
                "FLOOR": data_holder.floor_attribute,
                "ROOF": data_holder.roof_attribute,
                "APERTURE": data_holder.opening_attribute,
                "DOOR": data_holder.door_attribute,
            },
            data_holder.context if data_holder.context_mode == "COLLECTION" else None,
        )
        if model is None:
            return False

        hb.describe_model(model, len(sensors))

        grid = hb.sensor_grid("refops_grid", sensors)
        if grid is None:
            print("[RefOps.honeybee] Grade de sensores vazia.")
            return False
        try:
            model.properties.radiance.add_sensor_grid(grid)
        except Exception as e:
            print(f"[RefOps.honeybee] grade não anexada ao modelo: {e}")
            return False

        recipe_name, outputs = RECIPES[data_holder.analysis_type]
        folder = os.path.join(tempfile.gettempdir(), "refops_honeybee")
        inputs = cls._recipe_inputs(data_holder, model)
        if inputs is None:
            return False

        project = hb.run_recipe(
            recipe_name,
            inputs,
            folder=folder,
            workers=data_holder.workers or None,
        )
        if project is None:
            return False

        # A recipe that fails still returns its project folder, so reading
        # results from it produces "invalid result folder" and blames the path
        # for a simulation that never ran. The log decides which it was.
        if not hb.run_succeeded(project):
            print("[RefOps.honeybee] A simulação falhou — nenhum resultado gerado.")
            cls._report_logs(project)
            return False

        wrote = cls._write_outputs(
            target, domain, indices, data_holder, recipe_name, outputs, project
        )
        if not wrote:
            return False

        cls._assign_material(target, data_holder.display_material)
        cls._write_metadata(target, data_holder, domain, wrote)
        return True

    @classmethod
    def _report_logs(cls, project):
        """Print the tail of the recipe's error log.

        A failed Radiance task ends with Luigi announcing failures and a path —
        the actual reason sits in err.log, and nobody reads a file they were
        only told about."""
        for name in ("err.log", "logs.log"):
            path = os.path.join(project, "__logs__", name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    tail = handle.read()[-2000:].strip()
            except Exception:
                continue
            if not tail:
                continue
            print(f"[RefOps.honeybee] --- {name} ---")
            print(tail)
            if "No such option" in tail or "no such command" in tail.lower():
                # The recipe calls the honeybee-radiance CLI with flags that
                # version doesn't have. Nothing in the model or the inputs can
                # fix it — the two packages have to come from the same install.
                print(
                    "[RefOps.honeybee] A receita e o honeybee-radiance instalado "
                    "são de versões incompatíveis. Reinstale o grupo Honeybee "
                    "pelo botão nas preferências (com dependências), não com "
                    "--no-deps."
                )
            return

    # --- INPUTS ---

    @classmethod
    def _recipe_inputs(cls, data_holder, model):
        """What each recipe needs beyond the model.

        Daylight factor runs under a standard CIE overcast sky, so it takes no
        weather data — which is also why the EPW field hides for it."""
        inputs = {"model": model}
        analysis = data_holder.analysis_type

        if analysis == "DAYLIGHT_FACTOR":
            return inputs

        path = bpy.path.abspath(data_holder.epw) if data_holder.epw else ""
        if not path or not os.path.isfile(path):
            print("[RefOps.honeybee] EPW inválido ou não informado.")
            return None

        if analysis in {"ANNUAL_DAYLIGHT", "ANNUAL_IRRADIANCE"}:
            try:
                from ladybug.wea import Wea

                inputs["wea"] = Wea.from_epw_file(path)
            except Exception as e:
                print(f"[RefOps.honeybee] Wea: {e}")
                return None
            return inputs

        # Point in time: a single sky for one moment.
        CertainIlluminance = hb.optional(
            "honeybee_radiance.lightsource.sky.certainirradiance",
            "CertainIrradiance",
        )
        ClimateBased = hb.optional(
            "honeybee_radiance.lightsource.sky.climatebased", "ClimateBased"
        )
        if ClimateBased is None:
            print("[RefOps.honeybee] Céu pontual indisponível nesta versão.")
            return None
        try:
            from ladybug.wea import Wea

            wea = Wea.from_epw_file(path)
            sky = ClimateBased.from_wea(
                wea,
                int(data_holder.month),
                int(data_holder.day),
                float(data_holder.hour),
            )
            inputs["sky"] = sky
            inputs["metric"] = "illuminance"
        except Exception as e:
            print(f"[RefOps.honeybee] céu pontual: {e}")
            return None
        return inputs

    # --- TARGET ---

    @classmethod
    def _resolve_target(cls, source, data_holder):
        """(object receiving attributes, domain, sensors, element indices).

        Sensors come from the RESULT geometry: a separate result object is a
        sampling grid laid over the model, and measuring it is the point."""
        offset = max(data_holder.grid_offset, 0.001)

        if data_holder.result_geometry_mode == "OBJECT":
            target = data_holder.result_geometry
            if target is None or target.type != "MESH":
                print("[RefOps.honeybee] Result Object vazio ou não é malha.")
                return None, "FACE", [], []
            if data_holder.result_domain in {"VERTEX", "VERTEX_GROUP"}:
                sensors = lb.vertex_sensors(target, offset)
                domain = data_holder.result_domain
            else:
                sensors = lb.face_sensors(
                    target, list(range(len(target.data.polygons))), offset
                )
                domain = "FACE"
            return target, domain, sensors, [index for index, _p, _n in sensors]

        if data_holder.result_face_mode == "ALL_FACES":
            indices = list(range(len(source.data.polygons)))
        else:
            indices = cls._selected_faces(source, data_holder.analysis_face_attribute)
            if not indices:
                return None, "FACE", [], []
        return source, "FACE", lb.face_sensors(source, indices, offset), indices

    @classmethod
    def _selected_faces(cls, obj, attribute_name):
        if not attribute_name:
            print("[RefOps.honeybee] Nenhum atributo de seleção informado.")
            return []
        mesh = obj.data
        attribute = mesh.attributes.get(attribute_name)
        if attribute is None:
            print(f"[RefOps.honeybee] Atributo '{attribute_name}' não existe.")
            return []
        if attribute.domain != "FACE" or attribute.data_type != "BOOLEAN":
            print(
                f"[RefOps.honeybee] '{attribute_name}' é {attribute.data_type}/"
                f"{attribute.domain}; precisa ser BOOLEAN/FACE."
            )
            return []
        flags = [False] * len(mesh.polygons)
        attribute.data.foreach_get("value", flags)
        selected = [index for index, flag in enumerate(flags) if flag]
        if not selected:
            print("[RefOps.honeybee] Nenhuma face selecionada pelo atributo.")
        return selected

    # --- OUTPUTS ---

    @classmethod
    def _write_outputs(cls, target, domain, indices, data_holder, recipe_name,
                       outputs, project):
        """Annual Daylight writes four attributes at once; the others write one
        under the user's chosen name. Returns {attribute name: values}."""
        as_group = domain == "VERTEX_GROUP"
        blender_domain = "POINT" if domain in {"VERTEX", "VERTEX_GROUP"} else "FACE"
        written = {}
        multiple = len(outputs) > 1

        for default_name, (output_key, folders) in outputs.items():
            values = hb.flatten_results(
                hb.recipe_output(recipe_name, project, output_key)
            )
            if not values:
                # The handler refused, or the output name doesn't exist in this
                # recipe version. The files are still on disk.
                values = hb.read_grid_results(project, *folders)
            if not values:
                print(
                    f"[RefOps.honeybee] Sem resultados para '{default_name}'. "
                    f"Procurei em: {', '.join(folders)}"
                )
                continue
            name = default_name if multiple else (
                (data_holder.output_attribute or "").strip() or default_name
            )
            if as_group:
                if lb.write_vertex_group(target, name, values, indices):
                    written[name] = values
            elif hb.write_face_values(target, name, values, indices, blender_domain):
                written[name] = values
        if not written:
            print("[RefOps.honeybee] Nenhum atributo escrito.")
        return written

    @classmethod
    def _assign_material(cls, obj, material):
        if material is None:
            return
        mesh = obj.data
        if material.name not in [m.name for m in mesh.materials if m]:
            mesh.materials.append(material)
        for slot in obj.material_slots:
            if slot.link != "DATA":
                slot.link = "DATA"

    @classmethod
    def _write_metadata(cls, obj, data_holder, domain, written):
        obj["refops_operator"] = "honeybee_radiance"
        obj["analysis_type"] = data_holder.analysis_type
        obj["epw_path"] = data_holder.epw or ""
        obj["context_mode"] = data_holder.context_mode
        obj["result_geometry_mode"] = data_holder.result_geometry_mode
        obj["result_domain"] = domain
        names = list(written)
        obj["refops_value_attribute"] = names[0] if names else ""
        obj["refops_value_attributes"] = ",".join(names)
        # hb_value_* mirrors the Ladybug operators' lb_value_* for the primary
        # attribute; Annual Daylight writes several, so each also gets its own.
        for position, (name, values) in enumerate(written.items()):
            stats = {
                "min": round(min(values), 4),
                "max": round(max(values), 4),
                "mean": round(sum(values) / len(values), 4),
            }
            for key, value in stats.items():
                obj[f"hb_{name}_{key}"] = value
                if position == 0:
                    obj[f"hb_value_{key}"] = value
            if position == 0:
                obj["hb_samples"] = len(values)
