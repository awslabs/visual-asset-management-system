# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Units and up axis of a conversion (the SOURCE_UNITS, TARGET_UNITS, SOURCE_UP_AXIS and
TARGET_UP_AXIS options of the templates).

Pure functions, no model library is imported. A conversion has

- a *source* convention: what the input file declares (USD, DAE, 3MF, glTF) or, for a format that
  declares nothing (STL, OBJ, PLY, OFF, XYZ), what the operator sets in SOURCE_UNITS and
  SOURCE_UP_AXIS; and
- a *target* convention, which depends on the output format:

  =========================  ==============================================================
  glb, gltf                  fixed: metres, Y up. TARGET_* other than m / y are refused.
  usd, usda, usdc, usdz      declared: ``metersPerUnit`` and ``upAxis`` are written.
  dae                        declared: ``<unit>`` and ``<up_axis>`` are written.
  3mf                        declared unit (micron, millimeter, centimeter, inch, foot, meter);
                             the format is always Z up, so the axis is fixed.
  obj, stl, ply, off         undeclared: a scale or rotation is applied to the vertices.
  =========================  ==============================================================

With the defaults (``auto`` and ``keep``) a conversion changes no number in the geometry, except that
a target whose convention is fixed (glTF: metres and Y up; 3MF: Z up) converts a source that declares
another one, which is what makes a USD in centimetres a correct GLB.

Z up to Y up is ``(x, y, z) -> (x, z, -y)`` and Y up to Z up is ``(x, y, z) -> (x, -z, y)``.
"""

from dataclasses import dataclass
from typing import Optional

# Metres per unit.
UNITS = {'mm': 0.001, 'cm': 0.01, 'm': 1.0, 'km': 1000.0, 'in': 0.0254, 'ft': 0.3048, 'yd': 0.9144}
SOURCE_UNIT_VALUES = ('auto',) + tuple(UNITS)
TARGET_UNIT_VALUES = ('keep',) + tuple(UNITS)
SOURCE_UP_AXIS_VALUES = ('auto', 'y', 'z')
TARGET_UP_AXIS_VALUES = ('keep', 'y', 'z')
# How the files a model names are handled (REFERENCE_HANDLING) and what a missing one does
# (MISSING_REFERENCES). auto is package for a .usdz output and flatten for every other.
REFERENCE_HANDLING_VALUES = ('auto', 'flatten', 'package', 'convert-linked', 'selected-only')
MISSING_REFERENCES_VALUES = ('skip', 'fail')
USD_INPUTS = ('.usd', '.usda', '.usdc', '.usdz')
USD_LAYER_INPUTS = ('.usd', '.usda', '.usdc')

# The configuration keys (camelCase, as the templates' configBody writes them), their allowed values
# and their defaults. The vamsSchema bundle test reads the value tuples by syntax tree and compares
# them with each template's enumValues.
OPTION_KEYS = {
    'sourceUnits': ('source_units', SOURCE_UNIT_VALUES),
    'targetUnits': ('target_units', TARGET_UNIT_VALUES),
    'sourceUpAxis': ('source_up_axis', SOURCE_UP_AXIS_VALUES),
    'targetUpAxis': ('target_up_axis', TARGET_UP_AXIS_VALUES),
    'referenceHandling': ('reference_handling', REFERENCE_HANDLING_VALUES),
    'missingReferences': ('missing_references', MISSING_REFERENCES_VALUES),
}
TEMPLATE_TAGS = {
    'sourceUnits': 'SOURCE_UNITS',
    'targetUnits': 'TARGET_UNITS',
    'sourceUpAxis': 'SOURCE_UP_AXIS',
    'targetUpAxis': 'TARGET_UP_AXIS',
    'referenceHandling': 'REFERENCE_HANDLING',
    'missingReferences': 'MISSING_REFERENCES',
}

FIXED_CONVENTION_OUTPUTS = ('.glb', '.gltf')
DECLARING_OUTPUTS = ('.usdc', '.usda', '.usdz', '.dae', '.3mf')
# glTF carries no unit or axis of its own: by specification it is metres and Y up.
GLTF_INPUTS = ('.glb', '.gltf')
# Inputs whose files declare (or, by specification, default) a unit and an up axis.
DECLARING_INPUTS = ('.usd', '.usda', '.usdc', '.usdz', '.dae', '.3mf', '.glb', '.gltf', '.fbx', '.step', '.stp')

# 3MF's `unit` attribute values and their size in metres.
THREEMF_UNITS = {'micron': 0.000001, 'millimeter': 0.001, 'centimeter': 0.01, 'inch': 0.0254,
                 'foot': 0.3048, 'meter': 1.0}


@dataclass(frozen=True)
class Options:
    source_units: str = 'auto'
    target_units: str = 'keep'
    source_up_axis: str = 'auto'
    target_up_axis: str = 'keep'
    reference_handling: str = 'auto'
    missing_references: str = 'skip'

    @staticmethod
    def from_config(config):
        """The options of an input configuration. A key that is missing, empty or null reads as its
        default, so a body written before the options existed runs unchanged; a value outside the
        allowed set is refused naming the key."""
        config = config if isinstance(config, dict) else {}
        values = {}
        for key, (field, allowed) in OPTION_KEYS.items():
            value = config.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if not isinstance(value, str) or value.strip().lower() not in allowed:
                raise ValueError(
                    f"{TEMPLATE_TAGS[key]} must be one of {', '.join(allowed)}; got {str(value)[:40]!r}.")
            values[field] = value.strip().lower()
        return Options(**values)

    @property
    def is_default(self):
        return self == Options()

    @property
    def changes_units_or_axis(self):
        return (self.source_units, self.target_units, self.source_up_axis, self.target_up_axis) != \
            ('auto', 'keep', 'auto', 'keep')

    def resolved_reference_handling(self, output_extension):
        """REFERENCE_HANDLING with auto resolved: package for a .usdz output (flatten when a unit or
        axis option is set, since package keeps the layers as authored), flatten otherwise."""
        if self.reference_handling != 'auto':
            return self.reference_handling
        if output_extension == '.usdz' and not self.changes_units_or_axis:
            return 'package'
        return 'flatten'


@dataclass(frozen=True)
class Plan:
    """What to do to the loaded geometry and what to declare in the output."""
    scale: float = 1.0
    rotation: Optional[str] = None            # 'z_to_y', 'y_to_z' or None
    meters_per_unit: Optional[float] = None   # declared by the output, None when it declares none
    up_axis: Optional[str] = None             # declared by the output, None when it declares none

    @property
    def changes_geometry(self):
        return self.scale != 1.0 or self.rotation is not None


def has_declared_convention(input_extension):
    return input_extension in DECLARING_INPUTS


def validate_request(input_extension, output_extension, options):
    """Refuse an illegal combination before anything is downloaded. Raises ``ValueError`` naming the
    option and what to set."""
    handling = options.resolved_reference_handling(output_extension)
    if handling == 'package':
        if output_extension != '.usdz':
            raise ValueError(f"REFERENCE_HANDLING package writes a .usdz package, so it cannot produce "
                             f"{output_extension}. Choose flatten, or the .usdz output format.")
        if input_extension in USD_INPUTS and options.changes_units_or_axis:
            raise ValueError("REFERENCE_HANDLING package keeps the USD layers as authored, so it cannot "
                             "change the unit or up axis. Choose flatten, or leave the unit and axis "
                             "options on auto and keep.")
    if handling == 'convert-linked':
        if output_extension not in ('.usdc', '.usda'):
            raise ValueError(f"REFERENCE_HANDLING convert-linked writes USD layers (.usdc or .usda), so "
                             f"it cannot produce {output_extension}. Choose flatten.")
        if input_extension not in USD_LAYER_INPUTS:
            raise ValueError(f"REFERENCE_HANDLING convert-linked converts the USD layers a .usd, .usda or "
                             f".usdc file names, so it cannot be used for a {input_extension} input. "
                             f"Choose flatten.")
        if options.changes_units_or_axis:
            raise ValueError("REFERENCE_HANDLING convert-linked keeps each layer's own unit and up axis, "
                             "so it cannot change them. Choose flatten, or leave the unit and axis "
                             "options on auto and keep.")
    if output_extension in FIXED_CONVENTION_OUTPUTS:
        if options.target_units not in ('keep', 'm'):
            raise ValueError(
                f"{output_extension} is always in metres, so TARGET_UNITS cannot be "
                f"{options.target_units}. Leave it on keep, or choose another output format.")
        if options.target_up_axis == 'z':
            raise ValueError(
                f"{output_extension} is always Y up, so TARGET_UP_AXIS cannot be z. "
                f"Leave it on keep, or choose another output format.")
    if output_extension == '.3mf':
        if options.target_units in ('km', 'yd'):
            raise ValueError(
                f"3MF does not support {options.target_units}. Choose mm, cm, m, in or ft as TARGET_UNITS.")
        if options.target_up_axis == 'y':
            raise ValueError(
                "3MF is always Z up: the 3MF core specification defines its coordinate system with Z as the "
                "vertical axis, so TARGET_UP_AXIS cannot be y. Leave it on keep or choose z.")
    if not has_declared_convention(input_extension):
        if options.target_units != 'keep' and options.source_units == 'auto':
            raise ValueError(
                f"A {input_extension} file declares no unit, so TARGET_UNITS {options.target_units} needs "
                f"SOURCE_UNITS to be set to the unit of its numbers.")
        if options.target_up_axis != 'keep' and options.source_up_axis == 'auto':
            raise ValueError(
                f"A {input_extension} file declares no up axis, so TARGET_UP_AXIS {options.target_up_axis} "
                f"needs SOURCE_UP_AXIS to be set to the up axis of the file.")


def declared_for(input_extension, meters_per_unit=None, up_axis=None):
    """``(meters_per_unit, up_axis)`` the input file declares. glTF is metres and Y up by
    specification; a format that declares nothing is ``(None, None)``. ``meters_per_unit`` and
    ``up_axis`` are what the reader found in the file."""
    if input_extension in GLTF_INPUTS:
        return 1.0, 'y'
    return meters_per_unit, up_axis


def plan(options, output_extension, declared_meters=None, declared_up=None):
    """The scale, rotation and output declaration for ``options`` and the output format, given what
    the source declares (``None`` for a part it does not declare)."""
    source_m = UNITS[options.source_units] if options.source_units != 'auto' else declared_meters
    source_up = options.source_up_axis if options.source_up_axis != 'auto' else declared_up

    # ---- units
    if output_extension in FIXED_CONVENTION_OUTPUTS:
        target_m = 1.0
    elif options.target_units != 'keep':
        target_m = UNITS[options.target_units]
    else:
        target_m = source_m
    if options.target_units != 'keep' and source_m is None:
        raise ValueError(f"The source file declares no unit, so TARGET_UNITS {options.target_units} "
                         f"needs SOURCE_UNITS to be set.")
    scale = source_m / target_m if (source_m is not None and target_m is not None) else 1.0
    if output_extension == '.3mf' and target_m is not None and not _is_3mf_unit(target_m):
        # A unit 3MF cannot declare (km, yd) is written in metres.
        scale, target_m = (source_m / 1.0 if source_m is not None else 1.0), 1.0

    # ---- axis
    rotation = None
    if output_extension in FIXED_CONVENTION_OUTPUTS:
        target_up = 'y'
    elif output_extension == '.3mf':
        target_up = 'z'
    elif options.target_up_axis != 'keep':
        target_up = options.target_up_axis
    else:
        target_up = source_up
    if options.target_up_axis != 'keep' and source_up is None:
        raise ValueError(f"The source file declares no up axis, so TARGET_UP_AXIS {options.target_up_axis} "
                         f"needs SOURCE_UP_AXIS to be set.")
    if source_up is not None and target_up is not None and source_up != target_up:
        rotation = 'z_to_y' if (source_up, target_up) == ('z', 'y') else 'y_to_z'

    if output_extension in DECLARING_OUTPUTS or output_extension in FIXED_CONVENTION_OUTPUTS:
        # An output that declares a convention always states one: a source that declares none is
        # written as metres (millimetres for 3MF, its default) and Y up (Z up for 3MF) without
        # changing any number.
        if output_extension == '.3mf':
            out_m = target_m if target_m is not None else UNITS['mm']
            out_up = 'z'
        else:
            out_m = target_m if target_m is not None else 1.0
            out_up = target_up if target_up is not None else 'y'
        return Plan(scale=scale, rotation=rotation, meters_per_unit=out_m, up_axis=out_up)
    return Plan(scale=scale, rotation=rotation)


def _is_3mf_unit(meters):
    return any(abs(meters - value) < 1e-12 for value in THREEMF_UNITS.values())


def threemf_unit_name(meters):
    for name, value in THREEMF_UNITS.items():
        if abs(meters - value) < 1e-12:
            return name
    raise ValueError(f"3MF cannot declare a unit of {meters} metres")


def transform_matrix(the_plan):
    """The 4x4 matrix (as nested lists, row-major) applying the plan's scale and rotation, or None
    when the plan changes nothing."""
    if not the_plan.changes_geometry:
        return None
    s = the_plan.scale
    if the_plan.rotation == 'z_to_y':
        rows = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]
    elif the_plan.rotation == 'y_to_z':
        rows = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    else:
        rows = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    return [[s * rows[0][0], s * rows[0][1], s * rows[0][2], 0.0],
            [s * rows[1][0], s * rows[1][1], s * rows[1][2], 0.0],
            [s * rows[2][0], s * rows[2][1], s * rows[2][2], 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def meters_per_unit_from_name(name):
    """Metres per unit for a unit name as trimesh or a 3MF file spells it ('millimeter', 'inch',
    '0.01 * meters', 'meters'), or None when it is not one this module knows."""
    if not isinstance(name, str):
        return None
    text = name.strip().lower()
    if text in THREEMF_UNITS:
        return THREEMF_UNITS[text]
    if text in ('meters', 'metres', 'metre'):
        return 1.0
    if '*' in text:
        factor, _, unit = text.partition('*')
        if unit.strip() in ('meters', 'metres', 'meter'):
            try:
                value = float(factor)
            except ValueError:
                return None
            return value if value > 0 else None
    for short, value in (('millimeters', 0.001), ('centimeters', 0.01), ('inches', 0.0254), ('feet', 0.3048),
                         ('kilometers', 1000.0), ('yards', 0.9144)):
        if text == short:
            return value
    return None
