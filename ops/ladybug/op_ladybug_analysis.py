"""Ladybug climate analysis written back onto the parent's faces.

No geometry is produced. The parent object is the analysed model and the result
is a FLOAT attribute on its faces, which a display material reads through a
Color Ramp:

    Blender mesh -> face BOOL attribute -> Ladybug -> face FLOAT attribute

Blender's +Y is north, always, and Ladybug measures north as a clockwise angle
from +Y — so the offset is zero and there is no north input.

Scope: this operator covers what can be computed from geometry, an EPW and face
attributes. Daylight Factor and Illuminance need a full Radiance run over a
model with materials and apertures; they belong to the planned honeybee_radiance
operator and report a controlled warning here rather than returning a number
nobody should trust.
"""

import math

import bpy

import refops_api

lb = refops_api.ladybug_helpers


class LadybugAnalysis:
    id = "LADYBUG_ANALYSIS"
    label = "Ladybug Analysis"
    icon = "LIGHT_SUN"
    data_type = "MESH"
    execution_mode = "OBJECT"

    # Which analyses need what. Adding a row here and a builder below is the
    # whole cost of a new analysis type.
    NEEDS_EPW = {"DIRECT_SUN_HOURS", "SOLAR_RADIATION", "ILLUMINANCE", "OUTDOOR_COMFORT"}
    NEEDS_PERIOD = {"DIRECT_SUN_HOURS", "SOLAR_RADIATION", "ILLUMINANCE", "OUTDOOR_COMFORT"}

    props_definition = {
        "analysis_type": (
            "Enum",
            {
                "name": "Analysis",
                "items": [
                    ("DIRECT_SUN_HOURS", "Direct Sun Hours", "Hours of unobstructed sun"),
                    ("SOLAR_RADIATION", "Solar Radiation", "Incident radiation (needs Radiance)"),
                    ("DAYLIGHT_FACTOR", "Daylight Factor", "Belongs to honeybee_radiance"),
                    ("ILLUMINANCE", "Illuminance", "Belongs to honeybee_radiance"),
                    ("OUTDOOR_COMFORT", "Outdoor Comfort", "UTCI or PMV per face"),
                ],
                "default": "DIRECT_SUN_HOURS",
            },
        ),
        "sep1": ("Separator", {}),
        "lbl_faces": ("Label", {"text": "Result Geometry", "icon": "FACESEL"}),
        "result_geometry_mode": (
            "Enum",
            {
                "name": "Write To",
                "items": [
                    ("SELF", "This Object", "Results land on the analysed object"),
                    ("OBJECT", "Other Object", "Results land on a separate object"),
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
                "description": "Face BOOL attribute; only True faces receive results",
                "show_if": [("result_geometry_mode", {"SELF"}),
                            ("result_face_mode", {"SELECTED_FACES"})],
            },
        ),
        "result_geometry": (
            "Object",
            {
                "name": "Result Object",
                "show_if": ("result_geometry_mode", {"OBJECT"}),
            },
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
            {"name": "Output Attribute", "default": "sun_hours"},
        ),
        "display_material": ("Material", {"name": "Display Material"}),
        "sep2": ("Separator", {}),
        "lbl_source": ("Label", {"text": "Source", "icon": "FILE_FOLDER"}),
        "epw": (
            "Path",
            {
                "name": "EPW File",
                "path_type": "FILE",
                "show_if": (
                    "analysis_type",
                    {"DIRECT_SUN_HOURS", "SOLAR_RADIATION", "ILLUMINANCE", "OUTDOOR_COMFORT"},
                ),
            },
        ),
        "period_mode": (
            "Enum",
            {
                "name": "Period",
                "items": [
                    ("YEAR", "All Year", ""),
                    ("DAY", "All Day", "Every day, restricted hours"),
                    ("CUSTOM", "Custom Period", ""),
                ],
                "default": "YEAR",
                "show_if": (
                    "analysis_type",
                    {"DIRECT_SUN_HOURS", "SOLAR_RADIATION", "ILLUMINANCE", "OUTDOOR_COMFORT"},
                ),
            },
        ),
        "start_month": ("Int", {"name": "Start Month", "default": 1, "min": 1, "max": 12,
                                "show_if": ("period_mode", {"CUSTOM"})}),
        "start_day": ("Int", {"name": "Start Day", "default": 1, "min": 1, "max": 31,
                              "show_if": ("period_mode", {"CUSTOM"})}),
        "start_hour": ("Int", {"name": "Start Hour", "default": 0, "min": 0, "max": 23,
                               "show_if": ("period_mode", {"CUSTOM", "DAY"})}),
        "end_month": ("Int", {"name": "End Month", "default": 12, "min": 1, "max": 12,
                              "show_if": ("period_mode", {"CUSTOM"})}),
        "end_day": ("Int", {"name": "End Day", "default": 31, "min": 1, "max": 31,
                            "show_if": ("period_mode", {"CUSTOM"})}),
        "end_hour": ("Int", {"name": "End Hour", "default": 23, "min": 0, "max": 23,
                             "show_if": ("period_mode", {"CUSTOM", "DAY"})}),
        "sep3": ("Separator", {}),
        "lbl_context": ("Label", {"text": "Context", "icon": "OUTLINER_COLLECTION"}),
        "context_mode": (
            "Enum",
            {
                "name": "Context",
                "items": [
                    ("NONE", "None", "Only the parent object obstructs"),
                    ("COLLECTION", "Collection", "Add a collection as obstruction"),
                ],
                "default": "NONE",
            },
        ),
        "context": (
            "Collection",
            {"name": "Context Collection", "show_if": ("context_mode", {"COLLECTION"})},
        ),
        "sep4": ("Separator", {}),
        "lbl_tuning": ("Label", {"text": "Resolution", "icon": "PREFERENCES"}),
        "sun_timestep": (
            "Int",
            {
                "name": "Sample Every N Hours",
                "default": 1,
                "min": 1,
                "max": 24,
                "description": "Cost is faces x suns (BVH); raise this to preview",
                "show_if": ("analysis_type", {"DIRECT_SUN_HOURS", "OUTDOOR_COMFORT"}),
            },
        ),
        "comfort_model": (
            "Enum",
            {
                "name": "Comfort Model",
                "items": [
                    ("UTCI", "UTCI", "Universal Thermal Climate Index"),
                    ("PMV", "PMV", "Predicted Mean Vote"),
                ],
                "default": "UTCI",
                "show_if": ("analysis_type", {"OUTDOOR_COMFORT"}),
            },
        ),
        "comfort_metric": (
            "Enum",
            {
                "name": "Report",
                "items": [
                    ("MEAN", "Mean Value", "Average index over the period"),
                    ("COMFORTABLE_HOURS", "Comfortable Hours", "Hours inside the comfort range"),
                    ("COMFORTABLE_PERCENT", "Comfortable %", "Share of hours in range"),
                ],
                "default": "MEAN",
                "show_if": ("analysis_type", {"OUTDOOR_COMFORT"}),
            },
        ),
        "met_rate": ("Float", {"name": "Metabolic Rate (met)", "default": 2.4, "min": 0.1,
                               "show_if": [("analysis_type", {"OUTDOOR_COMFORT"}),
                                           ("comfort_model", {"PMV"})]}),
        "clo_value": ("Float", {"name": "Clothing (clo)", "default": 0.7, "min": 0.0,
                                "show_if": [("analysis_type", {"OUTDOOR_COMFORT"}),
                                            ("comfort_model", {"PMV"})]}),
        "sun_mrt_delta": (
            "Float",
            {
                "name": "MRT Rise in Sun (C)",
                "default": 15.0,
                "min": 0.0,
                "description": "Added to air temperature when the face sees the sun",
                "show_if": ("analysis_type", {"OUTDOOR_COMFORT"}),
            },
        ),
    }

    # --- ENTRY POINT ---

    @classmethod
    def execute(cls, container_obj, data_holder):
        if not lb.ensure():
            print("[RefOps.ladybug] Ladybug Tools indisponível:")
            print(lb.IMPORT_ERROR)
            return False

        parent_obj = bpy.data.objects.get(getattr(container_obj, "proc_parent_name", ""))
        if not parent_obj or parent_obj.type != "MESH":
            return False

        # The analysed geometry is the CONTAINER — the engine's copy of the
        # parent with its modifiers baked. Writing to the parent itself would be
        # undone by the next refresh, and the container is what any downstream
        # operation in the chain reads.
        source = container_obj
        source.display_type = "TEXTURED"
        source.hide_render = False

        target, domain, sensors = cls._resolve_target(source, data_holder)
        if not sensors:
            return False

        output_name = (data_holder.output_attribute or "").strip()
        if not output_name:
            print("[RefOps.ladybug] Nome do atributo de saída vazio.")
            return False

        builder = {
            "DIRECT_SUN_HOURS": cls._direct_sun_hours,
            "SOLAR_RADIATION": cls._solar_radiation,
            "DAYLIGHT_FACTOR": cls._radiance_only,
            "ILLUMINANCE": cls._radiance_only,
            "OUTDOOR_COMFORT": cls._outdoor_comfort,
        }[data_holder.analysis_type]

        try:
            # Obstructions always come from the analysed object plus the
            # context; the sensors decide where results are measured, and those
            # are two separate questions.
            results = builder(source, sensors, data_holder)
        except Exception as e:
            import traceback

            print(f"[RefOps.ladybug] '{data_holder.analysis_type}' falhou: {e}")
            traceback.print_exc()
            return False

        if not results:
            return False

        if domain == "VERTEX_GROUP":
            # Checked: a group that failed to be written used to leave the
            # operation reporting success with nothing on the object.
            if not lb.write_vertex_group(
                target, output_name, list(results.values()), list(results.keys())
            ):
                print(
                    f"[RefOps.ladybug] Vertex group '{output_name}' não foi "
                    f"escrito em '{target.name}'."
                )
                return False
        else:
            # Blender's vertex domain is called POINT in the attribute API.
            cls._write_attribute(
                target, output_name, results, "POINT" if domain == "VERTEX" else "FACE"
            )
        cls._assign_material(target, data_holder.display_material)
        cls._write_metadata(target, data_holder, output_name, results, domain)
        return True

    # --- TARGET RESOLUTION ---

    @classmethod
    def _resolve_target(cls, source, data_holder):
        """(object receiving the attribute, domain, sensors).

        Sensors are taken from the RESULT geometry, not the analysed one — a
        separate result object is usually a sampling grid laid over the model,
        and the point of it is to measure where that grid sits."""
        if data_holder.result_geometry_mode == "OBJECT":
            target = data_holder.result_geometry
            if target is None or target.type != "MESH":
                print("[RefOps.ladybug] Result Object vazio ou não é malha.")
                return None, "FACE", []
            domain = data_holder.result_domain
            if domain in {"VERTEX", "VERTEX_GROUP"}:
                sensors = lb.vertex_sensors(target)
            else:
                indices = list(range(len(target.data.polygons)))
                sensors = lb.face_sensors(target, indices)
            if not sensors:
                print("[RefOps.ladybug] Result Object sem geometria utilizável.")
            return target, domain, sensors

        if data_holder.result_face_mode == "ALL_FACES":
            indices = list(range(len(source.data.polygons)))
        else:
            indices = cls._selected_faces(source, data_holder.analysis_face_attribute)
            if indices is None:
                return None, "FACE", []
            if not indices:
                print("[RefOps.ladybug] Nenhuma face selecionada pelo atributo.")
                return None, "FACE", []
        return source, "FACE", lb.face_sensors(source, indices)

    # --- FACE SELECTION ---

    @classmethod
    def _selected_faces(cls, obj, attribute_name):
        """Indices of the faces flagged True. None means the input is invalid,
        which is a different outcome from an empty selection."""
        if not attribute_name:
            print("[RefOps.ladybug] Nenhum atributo de seleção informado.")
            return None
        mesh = obj.data
        attribute = mesh.attributes.get(attribute_name)
        if attribute is None:
            print(f"[RefOps.ladybug] Atributo '{attribute_name}' não existe.")
            return None
        if attribute.domain != "FACE" or attribute.data_type != "BOOLEAN":
            print(
                f"[RefOps.ladybug] '{attribute_name}' é "
                f"{attribute.data_type}/{attribute.domain}; precisa ser BOOLEAN/FACE."
            )
            return None
        flags = [False] * len(mesh.polygons)
        attribute.data.foreach_get("value", flags)
        return [index for index, flag in enumerate(flags) if flag]

    # --- OBSTRUCTIONS ---

    @classmethod
    def _obstacles(cls, obj, data_holder):
        """The parent always obstructs; a context collection adds to it.

        The same geometry as before, handed over as a BVH instead of a Face3D
        list: the ray test is the inner loop of every analysis here
        (sensors x vectors x faces) and the tree turns the per-ray cost from
        linear in the face count into a walk down the hierarchy."""
        objects = [obj]
        if data_holder.context_mode == "COLLECTION":
            objects.extend(lb.collection_meshes(data_holder.context))
        return lb.obstacle_bvh(objects)

    @classmethod
    def _sun_vectors(cls, data_holder):
        """Daylight sun vectors over the period, sampled every N hours.

        Returns (vector, hoy) pairs so a comfort model can look the weather up
        for the same hour the shadow was computed for."""
        from ladybug.sunpath import Sunpath

        epw = lb.load_epw(data_holder.epw)
        if epw is None:
            return None, None
        location = epw.location
        sunpath = Sunpath(
            location.latitude,
            location.longitude,
            location.time_zone,
            north_angle=lb.NORTH_ANGLE,
        )
        period = lb.analysis_period(data_holder)
        step = max(int(data_holder.sun_timestep), 1)

        vectors = []
        for position, hoy in enumerate(period.hoys):
            if position % step:
                continue
            sun = sunpath.calculate_sun_from_hoy(hoy)
            if not sun.is_during_day:
                continue
            vectors.append((sun.sun_vector.reverse(), hoy, sun))
        return vectors, epw

    # --- ANALYSES ---

    @classmethod
    def _direct_sun_hours(cls, source, sensors, data_holder):
        vectors, epw = cls._sun_vectors(data_holder)
        if not vectors:
            print("[RefOps.ladybug] Nenhum sol no período selecionado.")
            return None

        obstacles = cls._obstacles(source, data_holder)
        step = max(int(data_holder.sun_timestep), 1)

        # Ladybug -> mathutils once per sun, not once per (sensor, sun).
        rays = [(vector, lb.as_vector(vector)) for vector, _hoy, _sun in vectors]

        results = {}
        for index, origin, normal in sensors:
            ray_origin = lb.as_vector(origin)
            exposed = 0
            for vector, ray_vector in rays:
                # A face only sees a sun on its own side; the dot product test
                # skips most rays before any intersection work happens.
                if normal.dot(vector) <= 0:
                    continue
                if not lb.is_blocked_bvh(obstacles, ray_origin, ray_vector):
                    exposed += 1
            # Each sample stands for `step` hours of the period.
            results[index] = float(exposed * step)
        return results

    @classmethod
    def _solar_radiation(cls, source, sensors, data_holder):
        """Incident radiation from a sky matrix.

        SkyMatrix is generated by Radiance's gendaymtx, so this needs Radiance
        installed beyond the pip packages."""
        SkyMatrix = lb.optional("ladybug_radiance.skymatrix", "SkyMatrix")
        if SkyMatrix is None:
            print("[RefOps.ladybug] Radiação solar exige ladybug-radiance.")
            return None

        path = bpy.path.abspath(data_holder.epw) if data_holder.epw else ""
        if not path:
            print("[RefOps.ladybug] Nenhum EPW informado.")
            return None

        try:
            from ladybug.wea import Wea

            wea = Wea.from_epw_file(path)
            if data_holder.period_mode != "YEAR":
                wea = wea.filter_by_analysis_period(lb.analysis_period(data_holder))
            matrix = SkyMatrix(wea, north=lb.NORTH_ANGLE)
            patch_values, patch_vectors = cls._sky_patches(matrix)
        except Exception as e:
            print(f"[RefOps.ladybug] SkyMatrix: {e}")
            print("[RefOps.ladybug] Requer o Radiance instalado (gendaymtx).")
            return None

        if not patch_values:
            return None

        obstacles = cls._obstacles(source, data_holder)
        patches = [
            (value, vector, lb.as_vector(vector))
            for value, vector in zip(patch_values, patch_vectors)
        ]

        results = {}
        for index, origin, normal in sensors:
            ray_origin = lb.as_vector(origin)
            total = 0.0
            for value, vector, ray_vector in patches:
                cosine = normal.dot(vector)
                if cosine <= 0:
                    continue
                if lb.is_blocked_bvh(obstacles, ray_origin, ray_vector):
                    continue
                # Lambert's cosine law: a patch contributes in proportion to how
                # square-on it is to the face.
                total += value * cosine
            results[index] = total
        return results

    @classmethod
    def _sky_patches(cls, matrix):
        """(radiation per patch, direction per patch) from a SkyMatrix."""
        from ladybug_radiance.skymatrix import SkyMatrix  # noqa: F401

        values = None
        for attribute in ("total_values", "benefit_matrix", "values"):
            candidate = getattr(matrix, attribute, None)
            if candidate:
                values = list(candidate)
                break
        vectors = None
        for attribute in ("patch_vectors", "sky_dome_vectors"):
            candidate = getattr(matrix, attribute, None)
            if candidate:
                vectors = list(candidate)
                break
        if values is None or vectors is None:
            print(
                "[RefOps.ladybug] Não consegui ler os patches do SkyMatrix "
                "nesta versão — rode a sonda e me mande a saída."
            )
            return None, None
        count = min(len(values), len(vectors))
        return values[:count], vectors[:count]

    @classmethod
    def _outdoor_comfort(cls, source, sensors, data_holder):
        """UTCI or PMV per face.

        Mean radiant temperature is what makes this face-dependent: a surface in
        the sun runs hotter than the air, one in shade does not. There is no way
        to derive MRT rigorously without a radiation model, so the sun-exposure
        fraction computed here scales a user-set rise over air temperature. The
        assumption is explicit and adjustable rather than hidden."""
        model = data_holder.comfort_model
        module = "ladybug_comfort.utci" if model == "UTCI" else "ladybug_comfort.pmv"
        function_name = (
            "universal_thermal_climate_index" if model == "UTCI" else "predicted_mean_vote"
        )
        comfort_function = lb.optional(module, function_name)
        if comfort_function is None:
            print(f"[RefOps.ladybug] {model} exige ladybug-comfort.")
            return None

        vectors, epw = cls._sun_vectors(data_holder)
        if not vectors:
            return None

        period = lb.analysis_period(data_holder)
        air = lb.apply_period(epw.dry_bulb_temperature, period)
        wind = lb.apply_period(epw.wind_speed, period)
        humidity = lb.apply_period(epw.relative_humidity, period)
        hoys = list(air.header.analysis_period.hoys)
        weather = {
            round(hoy, 3): (temperature, speed, moisture)
            for hoy, temperature, speed, moisture in zip(
                hoys, air.values, wind.values, humidity.values
            )
        }

        obstacles = cls._obstacles(source, data_holder)
        metric = data_holder.comfort_metric
        rise = data_holder.sun_mrt_delta
        rays = [(vector, lb.as_vector(vector), hoy) for vector, hoy, _sun in vectors]

        results = {}
        for index, origin, normal in sensors:
            ray_origin = lb.as_vector(origin)
            samples = []
            for vector, ray_vector, hoy in rays:
                conditions = weather.get(round(hoy, 3))
                if conditions is None:
                    continue
                temperature, speed, moisture = conditions
                sunlit = normal.dot(vector) > 0 and not lb.is_blocked_bvh(
                    obstacles, ray_origin, ray_vector
                )
                mrt = temperature + (rise if sunlit else 0.0)
                # Station wind is measured at 10 m in the open; at body height
                # it is far lower, and UTCI is defined for 10 m anyway.
                speed = max(speed, 0.5)
                try:
                    if model == "UTCI":
                        samples.append(
                            comfort_function(temperature, mrt, speed, moisture)
                        )
                    else:
                        samples.append(
                            comfort_function(
                                temperature,
                                mrt,
                                min(speed, 1.0),
                                moisture,
                                data_holder.met_rate,
                                data_holder.clo_value,
                            )[0]
                        )
                except Exception as e:
                    print(f"[RefOps.ladybug] {model}: {e}")
                    return None
            results[index] = cls._reduce_comfort(samples, metric, model)
        return results

    @classmethod
    def _reduce_comfort(cls, samples, metric, model):
        if not samples:
            return 0.0
        if metric == "MEAN":
            return float(sum(samples) / len(samples))
        # Comfort ranges: UTCI 9-26 C is "no thermal stress"; PMV -0.5..+0.5 is
        # the ISO 7730 comfort band.
        if model == "UTCI":
            inside = sum(1 for value in samples if 9.0 <= value <= 26.0)
        else:
            inside = sum(1 for value in samples if -0.5 <= value <= 0.5)
        if metric == "COMFORTABLE_HOURS":
            return float(inside)
        return float(100.0 * inside / len(samples))

    @classmethod
    def _radiance_only(cls, source, sensors, data_holder):
        print(
            "[RefOps.ladybug] Daylight Factor e Illuminance exigem uma simulação "
            "Radiance sobre um modelo com materiais e aberturas — pertencem ao "
            "operador honeybee_radiance, não a este."
        )
        return None

    # --- WRITE-BACK ---

    @classmethod
    def _write_attribute(cls, obj, name, results, domain="FACE"):
        """Only the analysed elements are touched; every other value survives.

        An existing attribute is read back before writing so untouched elements
        keep whatever they had — replacing the whole array would silently wipe
        results from an earlier run on a different selection."""
        mesh = obj.data
        attribute = mesh.attributes.get(name)
        if attribute is not None and (
            attribute.data_type != "FLOAT" or attribute.domain != domain
        ):
            mesh.attributes.remove(attribute)
            attribute = None

        count = len(mesh.vertices) if domain == "POINT" else len(mesh.polygons)
        if attribute is None:
            attribute = mesh.attributes.new(name=name, type="FLOAT", domain=domain)
            values = [0.0] * count
        else:
            values = [0.0] * count
            attribute.data.foreach_get("value", values)

        for index, value in results.items():
            if index < count:
                values[index] = float(value)
        attribute.data.foreach_set("value", values)
        mesh.update()

    @classmethod
    def _assign_material(cls, obj, material):
        """The display material REPLACES what the output geometry carried.

        Appending only when absent left every earlier run's material in place:
        swapping the ramp added a second slot and the viewport kept showing the
        first, because the faces still pointed at material_index 0. Clearing
        first is what makes the input mean "this is the material".

        Only the slots are touched — the FLOAT attribute with the numbers lives
        in mesh.attributes and vertex-group results in obj.vertex_groups, and
        neither is affected by the material list."""
        if material is None:
            # Nothing to apply: leave whatever is there rather than stripping
            # materials the operator was never asked to manage.
            return
        mesh = obj.data
        mesh.materials.clear()
        mesh.materials.append(material)
        # One slot left, so index 0 is the only valid one — a face still
        # pointing at a slot that no longer exists renders untouched.
        if mesh.polygons:
            mesh.polygons.foreach_set("material_index", [0] * len(mesh.polygons))
        for slot in obj.material_slots:
            if slot.link != "DATA":
                slot.link = "DATA"

    @classmethod
    def _write_metadata(cls, obj, data_holder, output_name, results, domain="FACE"):
        values = list(results.values())
        obj["refops_operator"] = "ladybug_analysis"
        obj["analysis_type"] = data_holder.analysis_type
        obj["analysis_face_attribute"] = data_holder.analysis_face_attribute
        obj["output_attribute"] = output_name
        obj["epw_path"] = data_holder.epw or ""
        obj["period"] = data_holder.period_mode
        obj["context_mode"] = data_holder.context_mode
        obj["result_geometry_mode"] = data_holder.result_geometry_mode
        obj["result_domain"] = domain
        obj["refops_value_attribute"] = output_name
        if values:
            obj["lb_samples"] = len(values)
            obj["lb_value_min"] = round(min(values), 4)
            obj["lb_value_max"] = round(max(values), 4)
            obj["lb_value_mean"] = round(sum(values) / len(values), 4)
