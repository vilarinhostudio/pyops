"""Timing for operations that feel slow.

Two questions come up when a refresh drags, and they need different answers:
which OPERATION is slow, and which STEP inside it. The engine times every run;
an operator adds `with prof.step("name"):` around whatever it suspects, and both
land in the same report.

Nothing is measured unless the preference is on, so the cost when it is off is
one attribute read per operation.
"""

import time
from collections import defaultdict

import bpy

# Operation id -> {"runs": n, "total": seconds, "steps": {name: [n, seconds]}}
_records = defaultdict(lambda: {"runs": 0, "total": 0.0, "steps": defaultdict(lambda: [0, 0.0])})
_stack = []


def enabled():
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        return prefs.profile_operations
    except Exception:
        return False


class _Timer:
    __slots__ = ("label", "started", "operation")

    def __init__(self, label, operation=None):
        self.label = label
        self.operation = operation
        self.started = None

    def __enter__(self):
        if enabled():
            self.started = time.perf_counter()
        return self

    def __exit__(self, *exception):
        if self.started is None:
            return False
        elapsed = time.perf_counter() - self.started
        operation = self.operation or (_stack[-1] if _stack else "unknown")
        entry = _records[operation]["steps"][self.label]
        entry[0] += 1
        entry[1] += elapsed
        return False


class operation:
    """Times a whole operation and marks it as current, so steps inside know
    where to report."""

    __slots__ = ("name", "started")

    def __init__(self, name):
        self.name = name
        self.started = None

    def __enter__(self):
        _stack.append(self.name)
        if enabled():
            self.started = time.perf_counter()
        return self

    def __exit__(self, *exception):
        if _stack:
            _stack.pop()
        if self.started is None:
            return False
        elapsed = time.perf_counter() - self.started
        record = _records[self.name]
        record["runs"] += 1
        record["total"] += elapsed
        if enabled():
            print(f"[PyOps.prof] {self.name}: {elapsed:.3f}s")
            cls_report_steps(self.name, elapsed)
        return False


def step(label, operation_name=None):
    return _Timer(label, operation_name)


def cls_report_steps(name, elapsed):
    """Print the steps of one run, biggest first.

    Only steps that account for a real share are worth reading, so anything
    under 1% of the run is folded away rather than printed."""
    steps = _records[name]["steps"]
    if not steps:
        return
    ordered = sorted(steps.items(), key=lambda item: -item[1][1])
    for label, (count, total) in ordered:
        share = (total / elapsed * 100.0) if elapsed else 0.0
        if share < 1.0:
            continue
        print(f"[PyOps.prof]    {label}: {total:.3f}s em {count}x ({share:.0f}%)")


def report():
    """Everything measured so far, slowest operation first."""
    if not _records:
        print("[PyOps.prof] Nada medido ainda.")
        return
    print("[PyOps.prof] --- acumulado ---")
    for name, record in sorted(_records.items(), key=lambda item: -item[1]["total"]):
        runs = record["runs"] or 1
        print(
            f"[PyOps.prof] {name}: {record['total']:.3f}s em {record['runs']}x "
            f"(média {record['total'] / runs:.3f}s)"
        )
        for label, (count, total) in sorted(
            record["steps"].items(), key=lambda item: -item[1][1]
        ):
            print(f"[PyOps.prof]    {label}: {total:.3f}s em {count}x")


def reset():
    _records.clear()
    _stack.clear()


class PYOPS_OT_profile_report(bpy.types.Operator):
    bl_idname = "pyops.profile_report"
    bl_label = "Timing Report"
    bl_description = "Print accumulated timings to the console"
    bl_options = {"REGISTER"}

    def execute(self, context):
        report()
        self.report({"INFO"}, "Relatório no console do sistema.")
        return {"FINISHED"}


class PYOPS_OT_profile_reset(bpy.types.Operator):
    bl_idname = "pyops.profile_reset"
    bl_label = "Clear Timings"
    bl_options = {"REGISTER"}

    def execute(self, context):
        reset()
        self.report({"INFO"}, "Medições zeradas.")
        return {"FINISHED"}


classes = (PYOPS_OT_profile_report, PYOPS_OT_profile_reset)
