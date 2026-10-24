"""External Python packages for the analysis operators.

Blender's own site-packages is not always writable, and the folder Bonsai uses
can end up with half-installed packages that no amount of permissions will let
you delete — an import then succeeds while its submodules are missing, which is
the confusing failure this module exists to route around.

The answer is a folder the user owns. It goes at the FRONT of sys.path, so a
complete copy there wins over a broken one elsewhere without having to remove
anything.
"""

import os
import subprocess
import sys

import bpy

# Packages that Blender already ships. A second copy in the external folder
# would shadow Blender's own and is the fastest way to break the whole install,
# so they are removed after pip drags them in as dependencies.
SHADOWING = ("numpy", "cupy", "cupy_cuda12x", "cupy_backends")

GROUPS = {
    "LADYBUG": (
        "Ladybug (gráficos e análise climática)",
        ["lbt-ladybug"],
    ),
    "HONEYBEE": (
        "Honeybee + Radiance (receitas de iluminação)",
        [
            "lbt-recipes",
            "queenbee",
            "pollination-handlers",
            "honeybee-radiance-postprocess",
            "honeybee-display",
            "lbt-honeybee",
        ],
    ),
}


def library_folder():
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        return bpy.path.abspath(prefs.external_libs) if prefs.external_libs else ""
    except Exception:
        return ""


def activate(folder=None):
    """Put the external folder first on sys.path."""
    folder = folder or library_folder()
    if not folder or not os.path.isdir(folder):
        return False
    if folder in sys.path:
        sys.path.remove(folder)
    sys.path.insert(0, folder)
    return True


def installed(module_name):
    try:
        __import__(module_name)
        return True
    except Exception:
        return False


def report():
    """What each operator needs, and whether it is reachable right now."""
    return {
        "ladybug": installed("ladybug.epw"),
        "ladybug_radiance": installed("ladybug_radiance.skymatrix"),
        "honeybee": installed("honeybee.model"),
        "lbt_recipes": installed("lbt_recipes.recipe"),
        "pollination_handlers": installed("pollination_handlers.inputs"),
    }


def install(group, folder):
    """pip install into the external folder, then drop shadowing copies.

    Blender's bundled Python is used explicitly: the interpreter first on PATH
    is usually a different version, and packages built for it will not import
    here."""
    if not folder:
        print("[PyOps.deps] Nenhuma pasta externa definida.")
        return False

    os.makedirs(folder, exist_ok=True)
    packages = GROUPS[group][1]
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--target",
        folder,
        *packages,
    ]
    print(f"[PyOps.deps] {' '.join(command)}")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    except Exception as e:
        print(f"[PyOps.deps] pip falhou: {e}")
        return False

    if result.stdout:
        print(result.stdout[-4000:])
    if result.returncode != 0:
        print(result.stderr[-4000:])
        # pip reports failure when its cleanup step hits a locked file even
        # though the packages themselves landed, so this is a warning rather
        # than a verdict.
        print("[PyOps.deps] pip terminou com erro — verifique o status abaixo.")

    _drop_shadowing(folder)
    activate(folder)
    return True


def _drop_shadowing(folder):
    import shutil

    for name in os.listdir(folder):
        base = name.split("-")[0]
        if base in SHADOWING:
            path = os.path.join(folder, name)
            try:
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
                print(f"[PyOps.deps] removido (duplicaria o do Blender): {name}")
            except Exception as e:
                print(f"[PyOps.deps] não removeu {name}: {e}")


class PYOPS_OT_install_dependencies(bpy.types.Operator):
    bl_idname = "pyops.install_dependencies"
    bl_label = "Install"
    bl_description = "Install these packages into the external folder"
    bl_options = {"REGISTER"}

    group: bpy.props.StringProperty(default="LADYBUG")

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        folder = bpy.path.abspath(prefs.external_libs) if prefs.external_libs else ""
        if not folder:
            self.report({"ERROR"}, "Defina a pasta de bibliotecas primeiro.")
            return {"CANCELLED"}
        self.report({"INFO"}, "Instalando — acompanhe o console do sistema.")
        if install(self.group, folder):
            self.report({"INFO"}, "Concluído. Reinicie o Blender.")
            return {"FINISHED"}
        return {"CANCELLED"}


class PYOPS_OT_check_dependencies(bpy.types.Operator):
    bl_idname = "pyops.check_dependencies"
    bl_label = "Check"
    bl_description = "Print what is reachable right now to the console"
    bl_options = {"REGISTER"}

    def execute(self, context):
        activate()
        for name, ok in report().items():
            print(f"[PyOps.deps] {name:24} {'OK' if ok else 'AUSENTE'}")
        self.report({"INFO"}, "Resultado no console do sistema.")
        return {"FINISHED"}


classes = (PYOPS_OT_install_dependencies, PYOPS_OT_check_dependencies)
