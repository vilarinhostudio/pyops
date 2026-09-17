r"""Standalone EnergyPlus smoke test for the honeybee-energy that PyOps uses.

Runs outside Blender, against the same library folder the addon loads
(C:\refops_libs), so a pass here means the Blender side can reach EnergyPlus.
No OpenStudio: model_to_idf + run_idf, the path the planned operator takes,
which is why the HVAC is IdealAirSystem.

The IDF header mirrors honeybee_energy/cli/translate.py (Version + SimulationParameter
+ model); model_to_idf alone writes neither. Design days mirror cli/simulate.py
(DDY 99.6/0.4 %, falling back to EPW approximations), because zone sizing is on by
default and EnergyPlus aborts without design days.

Usage (Blender's Python works, it only needs the library folder):
    python tools/energyplus_smoke.py [EPW] [--libs C:\refops_libs]
"""
import argparse
import os
import re
import shutil
import sys
import tempfile

DEFAULT_EPW = r"C:\ladybug\BRA_Sao.Paulo-Congonhas.837800_SWERA\BRA_Sao.Paulo-Congonhas.837800_SWERA.epw"


def log(msg):
    print(f"[RefOps.energyplus] {msg}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("epw", nargs="?", default=DEFAULT_EPW)
    parser.add_argument("--libs", default=r"C:\refops_libs")
    args = parser.parse_args()

    # The addon's folder must win over any honeybee installed in this interpreter.
    sys.path.insert(0, args.libs)
    try:
        from importlib.metadata import version
        from honeybee.model import Model
        from honeybee.room import Room
        from honeybee_energy.config import folders
        from honeybee_energy.lib.programtypes import office_program
        from honeybee_energy.run import run_idf
        from honeybee_energy.simulation.parameter import SimulationParameter
        from honeybee_energy.writer import energyplus_idf_version, model_to_idf
        import honeybee_energy
    except ImportError as exc:
        log(f"import failed: {exc} — is honeybee-energy installed in {args.libs}?")
        return 1

    hbe_version = version("honeybee-energy")
    log(f"honeybee-energy {hbe_version} ({os.path.dirname(honeybee_energy.__file__)})")

    # folders searches C:\EnergyPlus* and the OpenStudio bundle; config.json overrides.
    if not folders.energyplus_path:
        log(r"EnergyPlus NOT found. honeybee-energy looks for C:\EnergyPlusV*, the "
            "EnergyPlus bundled with OpenStudio, or energyplus_path in "
            f"{os.path.join(os.path.dirname(honeybee_energy.__file__), 'config.json')}")
        return 2
    ep_version = folders.energyplus_version
    log(f"EnergyPlus {folders.energyplus_version_str} ({folders.energyplus_exe})")
    if ep_version is None:
        log("could not read the version from the executable (energyplus --version failed)")
        return 2

    if not os.path.isfile(args.epw):
        log(f"EPW not found: {args.epw}")
        return 1

    room = Room.from_box("SmokeBox", 5, 5, 3)
    room.properties.energy.program_type = office_program
    room.properties.energy.add_default_ideal_air()
    model = Model("SmokeModel", rooms=[room])

    sim_par = SimulationParameter()
    sim_par.output.add_zone_energy_use()
    ddy = os.path.splitext(args.epw)[0] + ".ddy"
    try:
        sim_par.sizing_parameter.add_from_ddy_996_004(ddy)
    except (AssertionError, OSError, IOError):
        from ladybug.epw import EPW
        epw_obj = EPW(args.epw)
        sim_par.sizing_parameter.design_days = [
            epw_obj.approximate_design_day("WinterDesignDay"),
            epw_obj.approximate_design_day("SummerDesignDay")]
        log("no usable DDY, design days approximated from the EPW")

    idf_version = energyplus_idf_version()
    idf_str = "\n\n".join([idf_version, sim_par.to_idf(), model_to_idf(model)])

    folder = tempfile.mkdtemp(prefix="pyops_ep_smoke_")
    idf = os.path.join(folder, "in.idf")
    with open(idf, "w") as f:
        f.write(idf_str)
    # Copy the EPW so run_idf's .stat renaming never touches the source folder.
    epw = shutil.copy(args.epw, folder)
    log(f"running in {folder}")

    sql, zsz, rdd, html, err = run_idf(idf, epw, silent=True)

    err_text = open(err, errors="replace").read() if err and os.path.isfile(err) else ""
    severe = len(re.findall(r"\*\* Severe", err_text))
    fatal = len(re.findall(r"\*\*  Fatal", err_text))
    completed = "EnergyPlus Completed Successfully" in err_text
    version_warn = [l.strip() for l in err_text.splitlines() if "Version:" in l and "IDF" in l]

    idf_ver = re.search(r"Version,\s*([\d.]+)", idf_version).group(1)
    exe_ver = ".".join(str(d) for d in ep_version)
    log("---- report ----")
    log(f"honeybee-energy : {hbe_version}")
    log(f"EnergyPlus exe  : {folders.energyplus_version_str}")
    log(f"IDF Version     : {idf_ver}")
    log(f"IDF == exe      : {idf_ver == exe_ver}")
    log(f"version warnings: {version_warn or 'none'}")
    log(f"severe / fatal  : {severe} / {fatal}")
    log(f"completed       : {completed}")
    log(f"sql             : {sql if sql and os.path.isfile(sql) else 'MISSING'}")
    log(f"err             : {err}")
    return 0 if completed and not fatal and sql and os.path.isfile(sql) else 3


if __name__ == "__main__":
    sys.exit(main())
