#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""B-rep loading and measurement through OCP, the Open CASCADE bindings cadquery ships.

The thumbnail path keeps tessellating STEP through cadquery. These helpers read STEP, IGES and BREP
into a TopoDS_Shape directly, tessellate it with the same tolerance, and count / measure it — with the
OCP module tree faked per test, since OCP is not installed where the tests run."""

import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy", reason="tessellation builds numpy arrays")

from preview_pipeline.format_handlers import cad_handler  # noqa: E402


class _FakeExplorer:
    """Iterates a fixed number of sub-shapes per TopAbs kind."""
    counts = {}

    def __init__(self, shape, kind):
        self.remaining = _FakeExplorer.counts.get(kind, 0)

    def More(self):
        return self.remaining > 0

    def Next(self):
        self.remaining -= 1

    def Current(self):
        return f"face-{self.remaining}"


class _FakeTriangulation:
    def NbNodes(self):
        return 3

    def NbTriangles(self):
        return 1

    def Node(self, i):
        return SimpleNamespace(X=lambda: float(i), Y=lambda: 0.0, Z=lambda: float(i) * 2)

    def Triangle(self, i):
        return SimpleNamespace(Get=lambda: (1, 2, 3))


def _install_fake_ocp(monkeypatch, read_status=1, kinds=None):
    """Install `OCP.*` fakes into sys.modules; returns a dict of the recorded calls."""
    calls = {"step": [], "iges": [], "brep": [], "meshed": []}
    kinds = kinds or {"SOLID": 2, "SHELL": 2, "FACE": 12, "WIRE": 12, "EDGE": 24, "VERTEX": 16}
    _FakeExplorer.counts = {f"TopAbs_{k}": v for k, v in kinds.items()}

    def module(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    class _Reader:
        kind = ""

        def ReadFile(self, path):
            calls[self.kind].append(path)
            return read_status

        def TransferRoots(self):
            pass

        def OneShape(self):
            return f"shape-from-{self.kind}"

    class _StepReader(_Reader):
        kind = "step"

    class _IgesReader(_Reader):
        kind = "iges"

    class _BRepTools:
        @staticmethod
        def Read_s(shape, path, builder):
            calls["brep"].append(path)
            return read_status == 1

    class _IncrementalMesh:
        def __init__(self, shape, tolerance):
            calls["meshed"].append((shape, tolerance))

        def Perform(self):
            pass

    class _BndBox:
        def Get(self):
            return (0.0, -1.0, 0.0, 10.0, 1.0, 5.0)

    class _GProps:
        def __init__(self):
            self.mass = 0.0

        def Mass(self):
            return self.mass

        def CentreOfMass(self):
            return SimpleNamespace(X=lambda: 5.0, Y=lambda: 0.0, Z=lambda: 2.5)

    class _BRepGProp:
        @staticmethod
        def VolumeProperties_s(shape, props):
            props.mass = 100.0

        @staticmethod
        def SurfaceProperties_s(shape, props):
            props.mass = 130.0

    monkeypatch.setitem(sys.modules, "OCP", types.ModuleType("OCP"))
    module("OCP.STEPControl", STEPControl_Reader=_StepReader)
    module("OCP.IGESControl", IGESControl_Reader=_IgesReader)
    module("OCP.BRepTools", BRepTools=_BRepTools)
    module("OCP.BRep", BRep_Builder=lambda: "builder",
           BRep_Tool=SimpleNamespace(Triangulation_s=lambda face, location: _FakeTriangulation()))
    module("OCP.TopoDS", TopoDS_Shape=lambda: "empty-shape",
           TopoDS=SimpleNamespace(Face=lambda shape: ("face", shape)))
    module("OCP.BRepMesh", BRepMesh_IncrementalMesh=_IncrementalMesh)
    module("OCP.TopExp", TopExp_Explorer=_FakeExplorer)
    module("OCP.TopAbs", **{f"TopAbs_{k}": f"TopAbs_{k}" for k in
                            ("SOLID", "SHELL", "FACE", "WIRE", "EDGE", "VERTEX")})
    module("OCP.TopLoc", TopLoc_Location=lambda: "location")
    module("OCP.Bnd", Bnd_Box=_BndBox)
    module("OCP.BRepBndLib", BRepBndLib=SimpleNamespace(Add_s=lambda shape, box, triangulation: None))
    module("OCP.GProp", GProp_GProps=_GProps)
    module("OCP.BRepGProp", BRepGProp=_BRepGProp)
    return calls


@pytest.mark.unit
class TestLoadShape:
    def test_extensions(self):
        assert cad_handler.SHAPE_EXTENSIONS == {".stp", ".step", ".iges", ".igs", ".brep"}
        assert cad_handler.SUPPORTED_EXTENSIONS == {".stp", ".step"}, "the thumbnail gate is unchanged"

    def test_step_uses_the_step_reader(self, monkeypatch):
        calls = _install_fake_ocp(monkeypatch)
        assert cad_handler.load_shape("/w/part.STEP") == "shape-from-step"
        assert calls["step"] == ["/w/part.STEP"] and calls["iges"] == [] and calls["brep"] == []

    def test_iges_uses_the_iges_reader(self, monkeypatch):
        calls = _install_fake_ocp(monkeypatch)
        assert cad_handler.load_shape("/w/part.igs") == "shape-from-iges"
        assert calls["iges"] == ["/w/part.igs"]

    def test_brep_uses_brep_tools(self, monkeypatch):
        calls = _install_fake_ocp(monkeypatch)
        assert cad_handler.load_shape("/w/part.brep") == "empty-shape"
        assert calls["brep"] == ["/w/part.brep"]

    def test_a_failed_read_raises_with_the_status(self, monkeypatch):
        _install_fake_ocp(monkeypatch, read_status=3)
        with pytest.raises(ValueError, match="status: 3"):
            cad_handler.load_shape("/w/part.stp")
        with pytest.raises(ValueError, match="BREP"):
            cad_handler.load_shape("/w/part.brep")

    def test_unknown_extension(self, monkeypatch):
        _install_fake_ocp(monkeypatch)
        with pytest.raises(ValueError, match="Unsupported CAD format"):
            cad_handler.load_shape("/w/part.obj")


@pytest.mark.unit
class TestTessellateAndMeasure:
    def test_tessellate_builds_polydata_at_the_tolerance(self, monkeypatch):
        calls = _install_fake_ocp(monkeypatch, kinds={"FACE": 2})
        made = {}

        def _polydata(vertices, faces):
            made["vertices"], made["faces"] = vertices, faces
            return "polydata"

        with patch.object(cad_handler, "pv", SimpleNamespace(PolyData=_polydata)):
            assert cad_handler.tessellate_shape("shape", tolerance=0.25) == "polydata"
        assert calls["meshed"] == [("shape", 0.25)]
        assert made["vertices"].shape == (6, 3)
        assert made["faces"].tolist() == [3, 0, 1, 2, 3, 3, 4, 5]

    def test_load_with_ocp_delegates(self, monkeypatch):
        calls = _install_fake_ocp(monkeypatch, kinds={"FACE": 1})
        with patch.object(cad_handler, "pv", SimpleNamespace(PolyData=lambda v, f: "polydata")):
            assert cad_handler._load_with_ocp("/w/part.step") == "polydata"
        assert calls["step"] == ["/w/part.step"]
        assert calls["meshed"] == [("shape-from-step", 0.1)]

    def test_statistics_count_every_topology_kind(self, monkeypatch):
        _install_fake_ocp(monkeypatch)
        assert cad_handler.shape_statistics("shape") == {
            "solids": 2, "shells": 2, "faces": 12, "wires": 12, "edges": 24, "vertices": 16}

    def test_geometry_from_the_bounding_box_and_mass_properties(self, monkeypatch):
        _install_fake_ocp(monkeypatch)
        assert cad_handler.shape_geometry("shape") == {
            "boundsMin": [0.0, -1.0, 0.0], "boundsMax": [10.0, 1.0, 5.0], "dimensions": [10.0, 2.0, 5.0],
            "volume": 100.0, "surfaceArea": 130.0, "centerOfMass": [5.0, 0.0, 2.5]}


@pytest.mark.unit
class TestUnits:
    @pytest.mark.parametrize("value,expected", [
        (0.0, "unknown"), (0.5, "meters (estimated)"), (50.0, "centimeters (estimated)"),
        (500.0, "millimeters (estimated)"), (5000.0, "unknown")])
    def test_estimated_units_heuristic(self, value, expected):
        assert cad_handler.estimated_units(value) == expected

    def test_declared_si_unit_with_prefix(self, tmp_path):
        path = tmp_path / "a.step"
        path.write_text("ISO-10303-21;\nDATA;\n#12=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));\nENDSEC;\n")
        assert cad_handler.declared_step_units(str(path)) == "millimetre"

    def test_declared_si_unit_without_prefix(self, tmp_path):
        path = tmp_path / "a.stp"
        path.write_text("#12=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT($,.METRE.));\n")
        assert cad_handler.declared_step_units(str(path)) == "metre"

    def test_declared_conversion_based_unit(self, tmp_path):
        path = tmp_path / "a.stp"
        path.write_text("#20=(CONVERSION_BASED_UNIT('INCH',#21)LENGTH_UNIT()NAMED_UNIT(#22));\n")
        assert cad_handler.declared_step_units(str(path)) == "inch"

    def test_no_declaration(self, tmp_path):
        path = tmp_path / "a.stp"
        path.write_bytes(b"\x00\x01binary-ish")
        assert cad_handler.declared_step_units(str(path)) is None
