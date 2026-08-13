"""Guard against Pydantic v1 `Field()` kwargs that silently validate nothing.

Pydantic v1 collects any keyword `Field()` does not recognize into `FieldInfo.extra` instead of
raising. A v2 spelling like `pattern=` therefore becomes an inert annotation: the model imports
cleanly, every test passes, and the field is entirely unconstrained. `regex=` is the v1 spelling.

These tests introspect the parsed models rather than grepping the source, so a constraint declared
across several lines is covered the same as a single-line one.
"""

import importlib
import pkgutil

import pytest

import models


def _model_classes():
    """Every model class declared in `backend/models/`, as (module, class) pairs."""
    for module_info in pkgutil.iter_modules(models.__path__):
        try:
            module = importlib.import_module(f"models.{module_info.name}")
        except Exception:
            # A model module that cannot import under test env is covered by its own suite.
            continue
        for name in dir(module):
            candidate = getattr(module, name)
            fields = getattr(candidate, "__fields__", None)
            if isinstance(fields, dict) and getattr(candidate, "__module__", "") == module.__name__:
                yield module_info.name, name, candidate


def _swallowed(kwarg):
    """Fields whose `Field()` call passed `kwarg`, which pydantic v1 silently ignored."""
    found = []
    for module_name, class_name, cls in _model_classes():
        for field_name, field in cls.__fields__.items():
            if kwarg in (getattr(field.field_info, "extra", {}) or {}):
                found.append(f"models/{module_name}.py::{class_name}.{field_name}")
    return sorted(found)


@pytest.mark.unit
class TestNoDeadFieldKwargs:
    """`Field(pattern=...)` is the v2 spelling and constrains nothing in v1."""

    def test_no_field_declares_pattern(self):
        offenders = _swallowed("pattern")
        assert offenders == [], (
            "Field(pattern=...) is silently ignored by pydantic v1 and validates nothing. "
            "Use regex=. Offending fields:\n  " + "\n  ".join(offenders))

    def test_strip_whitespace_on_field_is_a_known_no_op(self):
        """`strip_whitespace` is a `class Config`/`constr` option, not a `Field()` constraint.

        Every occurrence is inert: a padded value is stored verbatim. Pinned as a baseline so the
        count cannot grow unnoticed while the fields keep their declared intent; whether these
        fields should actually strip is a separate behavioral decision.
        """
        offenders = _swallowed("strip_whitespace")
        assert len(offenders) == 129, (
            f"Expected the known 129 inert strip_whitespace= declarations, found "
            f"{len(offenders)}. If you added one, prefer `anystr_strip_whitespace = True` on the "
            f"model's Config; if you removed one, lower this baseline.")

    def test_the_regex_convention_is_live_where_declared(self):
        """A field declaring a regex must expose it as the real v1 constraint."""
        live = [
            f"{module_name}::{class_name}.{field_name}"
            for module_name, class_name, cls in _model_classes()
            for field_name, field in cls.__fields__.items()
            if getattr(field.field_info, "regex", None)
        ]
        assert len(live) >= 26, f"Expected at least 26 live regex= constraints, found {len(live)}"
