"""Thermal simulation of one room through Honeybee-Energy + EnergyPlus.

The parent is the Room Reference: a closed mesh that IS the EnergyPlus zone.
Its faces are matched to the real Bonsai elements for layers and boundary
conditions, windows come from whatever elements carry Room=True in the
Openings pset, and the results land on the engine's copy as uniform FACE
float attributes:

    Room Reference + elements -> Honeybee Room -> EnergyPlus (x2) -> attributes

Two runs per execution: `free` (no HVAC, no setpoint — how the envelope alone
performs) and `active` (conditioned). Internal loads are identical in both, so
the difference between them is the HVAC and nothing else.

No OpenStudio: model_to_idf + run_idf, which is why HVAC is an IdealAirSystem.
Design and the decisions behind it: op_honeybee_energyplus.md.
"""

import os
import tempfile

import bpy

import refops_api

hb = refops_api.honeybee_helpers
bim = refops_api.bim_helpers

AIR_TEMP = "air_temp_mean"
COMFORT = "comfortable_hours_pct"
COOLING = "cooling_load_kwh"
HEATING = "heating_load_kwh"
ELECTRICITY = "electricity_kwh"

# What each pass reports. Loads and electricity only mean something with HVAC.
PASS_METRICS = {
    "free": (AIR_TEMP, COMFORT),
    "active": (AIR_TEMP, COMFORT, COOLING, HEATING, ELECTRICITY),
}


class HoneybeeEnergyPlus:
    id = "HONEYBEE_ENERGYPLUS"
    label = "Honeybee EnergyPlus"
    icon = "FORCE_TEXTURE"
    data_type = "MESH"
    execution_mode = "OBJECT"

    props_definition = {
        "lbl_room": ("Label", {"text": "Parent = Room Reference (closed mesh)", "icon": "MESH_CUBE"}),
        "room_name": (
            "String",
            {
                "name": "Room",
                "description": "Boolean property looked up by this name in each Room Pset below — not a Collection, so one element can serve more than one room",
            },
        ),
        "construction_pset": (
            "String",
            {
                "name": "Construction Pset",
                "default": "PyOps_RoomConstruction",
                "description": "Walls, slabs and roofs with Room=True in this pset; layers and IsExternal are read from each",
            },
        ),
        "layer_order": (
            "Enum",
            {
                "name": "Layer Order",
                "items": [
                    ("FIRST_OUTER", "First Layer Outside", "Layer 1 of the set faces the exterior"),
                    ("LAST_OUTER", "Last Layer Outside", "The last layer faces the exterior"),
                ],
                "default": "FIRST_OUTER",
            },
        ),
        "sep_openings": ("Separator", {}),
        "lbl_openings": ("Label", {"text": "Openings", "icon": "MOD_WIREFRAME"}),
        "openings_pset": (
            "String",
            {
                "name": "Openings Pset",
                "default": "PyOps_RoomOpenings",
                "description": "Windows and doors with Room=True in this pset; matched to the voids by position",
            },
        ),
        "opening_mode": (
            "Enum",
            {
                "name": "Voids From",
                "items": [
                    ("CUT", "Cut in Room Reference", "Holes in the Room Reference faces"),
                    ("PROJECTED", "Projected", "Voids of the Construction elements, projected onto the room"),
                ],
                "default": "PROJECTED",
            },
        ),
        "glazing_pset": (
            "String",
            {
                "name": "Glazing Pset",
                "default": "PyOps_Glazing",
                "description": "VisibleTransmittance, U_Factor, SHGC; VT ≤ 0.01 makes a door",
            },
        ),
        "sep_program": ("Separator", {}),
        "lbl_program": ("Label", {"text": "Program & Loads", "icon": "COMMUNITY"}),
        "program": (
            "String",
            {
                "name": "Program",
                "default": "Generic Office Program",
                "description": "honeybee-energy ProgramType identifier",
            },
        ),
        "enum_lights": (
            "Enum",
            {
                "name": "Lighting",
                "items": [
                    ("NONE", "None", "No lighting load — the program's density is zeroed"),
                    ("ROOM_PSET", "Room Pset", "Σ Wattage of the fixtures with Room=True, over the floor area"),
                ],
                "default": "NONE",
            },
        ),
        "light_room_pset": (
            "String",
            {
                "name": "Lights Pset",
                "default": "PyOps_RoomLights",
                "description": "Fixtures with Room=True in this pset",
                "show_if": ("enum_lights", {"ROOM_PSET"}),
            },
        ),
        "light_pset": (
            "String",
            {
                "name": "Light Pset",
                "default": "PyOps_Light",
                "description": "Wattage (W), or LuminousFlux (lm) at 90 lm/W",
                "show_if": ("enum_lights", {"ROOM_PSET"}),
            },
        ),
        "sep_hvac": ("Separator", {}),
        "lbl_hvac": ("Label", {"text": "Conditioning", "icon": "MOD_FLUID"}),
        "hvac_mode": (
            "Enum",
            {
                "name": "HVAC",
                "items": [
                    ("IDEAL_AIR", "Ideal Air", "Unlimited capacity; electricity is lights + equipment only"),
                    ("ROOM_PSET", "Room Pset", "Capacity and COP from the units' pset (approximate)"),
                ],
                "default": "IDEAL_AIR",
            },
        ),
        "heating_setpoint": ("Float", {"name": "Heating Setpoint (°C)", "default": 20.0}),
        "cooling_setpoint": ("Float", {"name": "Cooling Setpoint (°C)", "default": 24.0}),
        "hvac_room_pset": (
            "String",
            {
                "name": "HVAC Room Pset",
                "default": "PyOps_RoomHVAC",
                "description": "Units with Room=True in this pset",
                "show_if": ("hvac_mode", {"ROOM_PSET"}),
            },
        ),
        "hvac_pset": (
            "String",
            {
                "name": "HVAC Pset",
                "default": "PyOps_HVAC",
                "description": "CoolingCapacity, HeatingCapacity (W), COP_Cooling, COP_Heating",
                "show_if": ("hvac_mode", {"ROOM_PSET"}),
            },
        ),
        "sep_comfort": ("Separator", {}),
        "lbl_comfort": ("Label", {"text": "Comfort", "icon": "OUTLINER_OB_ARMATURE"}),
        "comfort_mode": (
            "Enum",
            {
                "name": "Criterion",
                "items": [
                    ("ADAPTIVE", "ASHRAE-55 Adaptive", "80 % acceptability on operative temperature"),
                    ("FIXED", "Fixed Band", "Operative temperature within a range"),
                ],
                "default": "ADAPTIVE",
            },
        ),
        "comfort_low": ("Float", {"name": "Min (°C)", "default": 18.0, "show_if": ("comfort_mode", {"FIXED"})}),
        "comfort_high": ("Float", {"name": "Max (°C)", "default": 26.0, "show_if": ("comfort_mode", {"FIXED"})}),
        "comfort_hours": (
            "Enum",
            {
                "name": "Hours",
                "items": [
                    ("ALL", "All Hours", "Every simulated hour"),
                    ("OCCUPIED", "Occupied Hours", "Hours with occupants in the program"),
                ],
                "default": "OCCUPIED",
            },
        ),
        "sep_period": ("Separator", {}),
        "lbl_period": ("Label", {"text": "Weather & Period", "icon": "TIME"}),
        "epw": ("Path", {"name": "EPW File", "path_type": "FILE"}),
        "granularity": (
            "Enum",
            {
                "name": "Period",
                "items": [
                    ("ANNUAL", "Annual", "One value per metric"),
                    ("MONTHLY", "Monthly", "Twelve attributes per metric, suffix _mMM"),
                    ("SINGLE_MONTH", "Single Month", "Simulates only that month, suffix _mMM"),
                ],
                "default": "ANNUAL",
            },
        ),
        "month": ("Int", {"name": "Month", "default": 1, "min": 1, "max": 12,
                          "show_if": ("granularity", {"SINGLE_MONTH"})}),
        "sep_result": ("Separator", {}),
        "lbl_result": (
            "Label",
            {"text": "Writes free_/active_* (face float) on this object", "icon": "FACESEL"},
        ),
        "display_material": ("Material", {"name": "Display Material"}),
    }

    # --- ENTRY POINT ---

    @classmethod
    def execute(cls, container_obj, data_holder):
        if not hb.ensure_energy():
            print("[RefOps.energyplus] honeybee-energy indisponível:")
            print(hb.ENERGY_IMPORT_ERROR)
            return False
        if not hb.energyplus_check():
            return False
        if not bim.ensure():
            print("[RefOps.energyplus] Bonsai indisponível — camadas e psets vêm do IFC.")
            return False

        epw = bpy.path.abspath(data_holder.epw) if data_holder.epw else ""
        if not epw or not os.path.isfile(epw):
            print("[RefOps.energyplus] EPW inválido ou não informado.")
            return False

        # The container is the parent with modifiers baked; it is the room.
        room, entries = hb.energy_room(container_obj)
        if room is None:
            return False
        program = hb.program_type(data_holder.program)
        if program is None:
            return False

        # Membership by Room pset, not Collection: a shared wall can be True
        # for two rooms at once, so its object can serve both runs' lists
        # without ever being moved between collections.
        construction = bim.room_members(data_holder.construction_pset, data_holder.room_name)
        if not construction:
            print("[RefOps.energyplus] Nenhum elemento de Construction marcado para este cômodo — todas as faces genéricas.")
        hb.match_room_faces(entries, construction)
        hb.apply_envelope(entries, data_holder.layer_order == "FIRST_OUTER")

        openings = bim.room_members(data_holder.openings_pset, data_holder.room_name)
        if data_holder.opening_mode == "CUT":
            added = hb.openings_cut(entries, openings, data_holder.glazing_pset)
        else:
            added = hb.openings_projected(entries, construction, openings, data_holder.glazing_pset)
        print(f"[RefOps.energyplus] {len(entries)} faces, {added} aberturas no Room.")

        program = cls._program(program, room, data_holder)
        if program is None:
            return False
        room.properties.energy.program_type = program

        hvac = cls._hvac(data_holder)
        if hvac is None:
            return False
        ideal_air, cops = hvac

        months = list(range(1, 13)) if data_holder.granularity == "MONTHLY" else None
        month = data_holder.month if data_holder.granularity == "SINGLE_MONTH" else None
        folder = os.path.join(tempfile.gettempdir(), "refops_energyplus")

        results = {}
        for name in ("free", "active"):
            summary = cls._run_pass(
                name, room, program, ideal_air, cops, data_holder, epw,
                os.path.join(folder, name), months, month,
            )
            if summary is None:
                return False
            results[name] = summary

        written = cls._write_results(container_obj, results, data_holder, month)
        if not written:
            return False
        cls._assign_material(container_obj, data_holder.display_material)
        container_obj["refops_operator"] = "honeybee_energyplus"
        container_obj["epw_path"] = data_holder.epw or ""
        container_obj["refops_value_attributes"] = ",".join(written)
        return True

    # --- MODEL ---

    @classmethod
    def _program(cls, base, room, data_holder):
        """The library program with the user's inputs on top.

        Lighting None zeroes the program's density: an explicit input wins over
        the library default. Setpoints replace the program's schedules with
        constants."""
        from honeybee_energy.load.lighting import Lighting
        from honeybee_energy.load.setpoint import Setpoint

        program = base.duplicate()
        program.identifier = f"{base.identifier}_refops"
        if data_holder.enum_lights == "NONE":
            program.lighting = None
        else:
            lights = bim.room_members(data_holder.light_room_pset, data_holder.room_name)
            watts = hb.lighting_watts(lights, data_holder.light_pset)
            area = room.floor_area
            if area <= 0:
                print("[RefOps.energyplus] Room sem piso — densidade de luz impossível.")
                return None
            if base.lighting is not None:
                schedule = base.lighting.schedule
            elif base.people is not None:
                schedule = base.people.occupancy_schedule
                print("[RefOps.energyplus] Program sem luz — agenda de ocupação usada para as luminárias.")
            else:
                schedule = hb.constant_schedule("refops_lights_on", 1.0, "Fractional")
                print("[RefOps.energyplus] Program sem luz nem ocupação — luminárias sempre ligadas.")
            program.lighting = Lighting("refops_lighting", watts / area, schedule)
            print(f"[RefOps.energyplus] Luz: {watts:.0f} W / {area:.1f} m² = {watts / area:.2f} W/m².")

        program.setpoint = Setpoint(
            "refops_setpoint",
            hb.constant_schedule("refops_heating_sp", data_holder.heating_setpoint),
            hb.constant_schedule("refops_cooling_sp", data_holder.cooling_setpoint),
        )
        return program

    @classmethod
    def _hvac(cls, data_holder):
        """(IdealAirSystem, (COP cooling, COP heating)) or None."""
        from honeybee_energy.hvac.idealair import IdealAirSystem

        if data_holder.hvac_mode == "IDEAL_AIR":
            return IdealAirSystem("refops_ideal_air"), (None, None)

        hvac_objects = bim.room_members(data_holder.hvac_room_pset, data_holder.room_name)
        cooling, heating, cop_cool, cop_heat = hb.hvac_readings(
            hvac_objects, data_holder.hvac_pset
        )
        if cooling <= 0 and heating <= 0:
            print(
                f"[RefOps.energyplus] Nenhum elemento HVAC deste cômodo com CoolingCapacity/HeatingCapacity "
                f"em {data_holder.hvac_pset} — nada para simular."
            )
            return None
        print(
            "[RefOps.energyplus] AVISO — HVAC Collection é aproximação: capacidade somada "
            "como limite do Ideal Air e COP agregado aplicado DEPOIS da simulação, sem "
            "variação com temperatura externa nem carga parcial. Compara aparelhos entre "
            "si; o consumo absoluto sai otimista."
        )
        print(
            f"[RefOps.energyplus] HVAC: resfriamento {cooling:.0f} W (COP {f'{cop_cool:.2f}' if cop_cool else '—'}), "
            f"aquecimento {heating:.0f} W (COP {f'{cop_heat:.2f}' if cop_heat else '—'})."
        )
        try:
            system = IdealAirSystem(
                "refops_ideal_air", heating_limit=heating, cooling_limit=cooling
            )
        except Exception as e:
            print(f"[RefOps.energyplus] IdealAirSystem com limites: {e}")
            return None
        return system, (cop_cool, cop_heat)

    # --- SIMULATION ---

    @classmethod
    def _run_pass(cls, name, room, program, ideal_air, cops, data_holder, epw,
                  folder, months, month):
        """Simulate one pass; {bucket: {metric: value}} or None."""
        from honeybee.model import Model

        active = name == "active"
        pass_program = program.duplicate()
        if not active:
            # Same internal loads, nothing holding the temperature.
            pass_program.setpoint = None
        pass_room = room.duplicate()
        pass_room.properties.energy.program_type = pass_program
        pass_room.properties.energy.hvac = ideal_air.duplicate() if active else None

        try:
            model = Model(f"refops_{name}", rooms=[pass_room], units="Meters",
                          tolerance=0.01, angle_tolerance=1.0)
        except Exception as e:
            print(f"[RefOps.energyplus] Model ({name}): {e}")
            return None

        print(f"[RefOps.energyplus] rodando passe '{name}'...")
        sql = hb.simulate_energy(model, epw, folder, month)
        if sql is None:
            return None
        series = hb.energy_series(sql)
        flags = hb.comfortable_flags(
            series, epw, data_holder.comfort_mode,
            data_holder.comfort_low, data_holder.comfort_high,
        )
        summary = hb.summarise_energy(
            series, flags, months,
            occupied_only=data_holder.comfort_hours == "OCCUPIED",
            cops=cops, hvac_electricity=data_holder.hvac_mode == "ROOM_PSET",
        )
        if not summary:
            print(f"[RefOps.energyplus] passe '{name}' sem séries no .sql.")
            return None
        return summary

    # --- OUTPUTS ---

    @classmethod
    def _write_results(cls, obj, results, data_holder, month):
        """Uniform FACE attributes plus hb_<name>_min/max/mean custom props.

        Monthly writes twelve attributes per metric and its min/max/mean are
        taken across the months; otherwise the three props equal the value."""
        count = len(obj.data.polygons)
        written = []
        for pass_name, buckets in results.items():
            for metric in PASS_METRICS[pass_name]:
                base = f"{pass_name}_{metric}"
                series = []
                for bucket, summary in sorted(buckets.items(), key=lambda b: b[0] or 0):
                    if metric not in summary:
                        continue
                    value = summary[metric]
                    suffix = bucket or month
                    name = f"{base}_m{suffix:02d}" if suffix else base
                    if hb.write_face_values(obj, name, [value] * count):
                        written.append(name)
                        series.append(value)
                if not series:
                    continue
                label = base if data_holder.granularity == "MONTHLY" else (
                    f"{base}_m{month:02d}" if month else base
                )
                obj[f"hb_{label}_min"] = round(min(series), 4)
                obj[f"hb_{label}_max"] = round(max(series), 4)
                obj[f"hb_{label}_mean"] = round(sum(series) / len(series), 4)
                if data_holder.granularity != "MONTHLY":
                    print(f"[RefOps.energyplus] {label} = {series[0]:.2f}")
        if not written:
            print("[RefOps.energyplus] Nenhum atributo escrito.")
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
