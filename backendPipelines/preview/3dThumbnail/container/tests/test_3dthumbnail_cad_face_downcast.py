#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""``tessellate_shape`` downcasts every explored sub-shape to a ``TopoDS_Face`` before triangulating it.

``TopExp_Explorer.Current()`` returns a ``TopoDS_Shape``; ``BRep_Tool.Triangulation_s`` is bound against
``TopoDS_Face`` only and rejects the base type with ``TypeError: incompatible function arguments``. The
``BRep_Tool`` fake here enforces that binding, so the test fails if the cast is dropped again. OCP is not
installed where the tests run, so its module tree is faked per test."""

import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("numpy", reason="tessellation builds numpy arrays")

from preview_pipeline.format_handlers import cad_handler  # noqa: E402


class _Shape:
    """Stands in for the TopoDS_Shape the explorer yields."""

    def __init__(self, index):
        self.index = index


class _Face:
    """Stands in for the TopoDS_Face the static cast returns."""

    def __init__(self, shape):
        self.shape = shape


class _Triangulation:
    def NbNodes(self):
        return 3

    def NbTriangles(self):
        return 1

    def Node(self, i):
        return SimpleNamespace(X=lambda: float(i), Y=lambda: 0.0, Z=lambda: 0.0)

    def Triangle(self, i):
        return SimpleNamespace(Get=lambda: (1, 2, 3))


def _install_fake_ocp(monkeypatch, face_count, cast_name):
    """Fake ``OCP.*`` for ``tessellate_shape`` with the face cast bound under ``cast_name``. Returns the
    recorded calls: the shapes cast, and the exact objects ``Triangulation_s`` received."""
    calls = {"cast": [], "triangulated": []}
    shapes = [_Shape(i) for i in range(face_count)]

    class _Explorer:
        def __init__(self, shape, kind):
            self.pending = list(shapes)

        def More(self):
            return bool(self.pending)

        def Next(self):
            self.pending.pop(0)

        def Current(self):
            return self.pending[0]

    def _cast(shape):
        calls["cast"].append(shape)
        return _Face(shape)

    def _triangulation(face, location):
        if not isinstance(face, _Face):
            raise TypeError(
                "Triangulation_s(): incompatible function arguments. Invoked with: "
                f"<{type(face).__name__}>, <TopLoc_Location>")
        calls["triangulated"].append(face)
        return _Triangulation()

    def module(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        monkeypatch.setitem(sys.modules, name, mod)

    monkeypatch.setitem(sys.modules, "OCP", types.ModuleType("OCP"))
    module("OCP.BRep", BRep_Tool=SimpleNamespace(Triangulation_s=_triangulation))
    module("OCP.BRepMesh", BRepMesh_IncrementalMesh=lambda shape, tol: SimpleNamespace(Perform=lambda: None))
    module("OCP.TopExp", TopExp_Explorer=_Explorer)
    module("OCP.TopAbs", TopAbs_FACE="TopAbs_FACE")
    module("OCP.TopLoc", TopLoc_Location=lambda: "location")
    module("OCP.TopoDS", TopoDS_Shape=lambda: "empty-shape", TopoDS=SimpleNamespace(**{cast_name: _cast}))
    return calls, shapes


@pytest.mark.unit
class TestFaceDowncast:
    @pytest.mark.parametrize("cast_name", ["Face", "Face_s"])
    def test_every_face_is_downcast_before_triangulation(self, monkeypatch, cast_name):
        calls, shapes = _install_fake_ocp(monkeypatch, face_count=3, cast_name=cast_name)

        with patch.object(cad_handler, "pv", SimpleNamespace(PolyData=lambda v, f: "polydata")):
            assert cad_handler.tessellate_shape("shape") == "polydata"

        assert calls["cast"] == shapes, "each explored sub-shape is passed to the cast, in order"
        assert [face.shape for face in calls["triangulated"]] == shapes, \
            "Triangulation_s receives the cast's return value for every face, never the raw shape"

    def test_the_uncast_shape_is_rejected_by_the_binding(self, monkeypatch):
        """The fake enforces the real binding so the assertion above cannot pass vacuously."""
        _install_fake_ocp(monkeypatch, face_count=1, cast_name="Face")
        from OCP.BRep import BRep_Tool
        with pytest.raises(TypeError, match="incompatible function arguments.*_Shape"):
            BRep_Tool.Triangulation_s(_Shape(0), "location")

    def test_face_downcast_prefers_the_ocp_7_8_name(self):
        both = SimpleNamespace(Face="new", Face_s="old")
        assert cad_handler.face_downcast(both) == "new"
        assert cad_handler.face_downcast(SimpleNamespace(Face_s="old")) == "old"
        with pytest.raises(AttributeError):
            cad_handler.face_downcast(SimpleNamespace())
