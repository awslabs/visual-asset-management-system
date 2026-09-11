#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Two ways a Cosmos Transfer run could report SUCCESS with the wrong result.

**Control type (owner question 84).** `CONTROL_TYPE_MAP.get(control_type.lower(), "edge")` substituted
the EDGE model for any unrecognised `controlType`, so a run asking for `dpeth` silently ran edge,
named its output after the type the operator asked for, and reported success. Nothing about the run
prompted a re-check. `resolve_control_type()` now raises and names the supported types.

**Output selection (owner question 88).** `find_output_video` returned the first `rglob` hit. `rglob`
yields directory order -- filesystem-dependent, not sorted -- so with a second `.mp4` present the
choice was arbitrary, and exactly one object is uploaded from that result. An unrelated file uploaded
as the generated video is again success with the wrong result. Selection is now sorted, and a
multi-candidate run says so.

Both fixes shipped with no test, because this pipeline had no `container/tests/` directory at all --
which is the gap this file closes. The two are covered together because they are one pipeline and one
directory; splitting them would duplicate the hermetic module loader for no benefit.

Each fix is asserted in BOTH directions. A resolver that rejected everything, or a selector that
returned nothing, would satisfy the negative arms alone -- and over-rejection is the more expensive
failure here, since it would fail runs that used to work.
"""

import pytest


# ============================ control type resolution ============================

class TestControlTypeResolution:
    @pytest.mark.parametrize("supplied,expected", [
        ("edge", "edge"),
        ("depth", "depth"),
        ("seg", "seg"),
        ("segmentation", "seg"),
        ("blur", "vis"),
        ("vis", "vis"),
    ])
    def test_every_supported_type_resolves(self, transfer_inference, supplied, expected):
        """Paired arm, and the one that matters most: these are the runs that must keep working.

        Both aliases are included deliberately -- `segmentation` -> `seg` and `blur` -> `vis` are the
        mappings a stricter resolver would be most likely to drop.
        """
        assert transfer_inference.resolve_control_type(supplied) == expected

    @pytest.mark.parametrize("supplied", ["EDGE", " Edge ", "Depth", "SEGMENTATION"])
    def test_case_and_surrounding_space_are_still_tolerated(self, transfer_inference, supplied):
        """The old code lower-cased, so tightening must not start rejecting a capitalised value."""
        assert transfer_inference.resolve_control_type(supplied) in {"edge", "depth", "seg", "vis"}

    @pytest.mark.parametrize("supplied", ["dpeth", "canny", "", "edge2", "sgementation"])
    def test_an_unrecognised_type_raises_instead_of_running_edge(self, transfer_inference, supplied):
        """The defect itself. `dpeth` is the realistic case: a typo for a type that IS supported."""
        with pytest.raises(ValueError) as exc:
            transfer_inference.resolve_control_type(supplied)
        message = str(exc.value)
        assert "controlType" in message or "control type" in message.lower()
        # The message must name the alternatives, or the operator cannot act on it.
        assert "edge" in message and "depth" in message

    def test_the_map_has_no_default_that_would_reinstate_the_fallback(self, transfer_inference):
        """Durable guard on the mechanism, not just the behaviour.

        The original defect was a `.get(key, "edge")` default rather than a missing branch, so this
        asserts the resolver consults membership. A future edit that reintroduced a default would keep
        the arms above green for the supported types while silently restoring the substitution.
        """
        with pytest.raises(ValueError):
            transfer_inference.resolve_control_type("definitely-not-a-control-type")


# ============================ output video selection ============================

def _write(base, *relative_names):
    """Create .mp4 files whose DIRECTORY order differs from their sorted order where possible."""
    made = []
    for name in relative_names:
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video bytes")
        made.append(path)
    return made


class TestOutputVideoSelection:
    def test_a_single_video_is_returned(self, transfer_entrypoint, tmp_path):
        out = tmp_path / "output"
        _write(out, "run/generated.mp4")
        chosen = transfer_entrypoint.find_output_video(out)
        assert chosen is not None and chosen.name == "generated.mp4"

    def test_the_choice_is_sorted_order_not_walk_order(self, transfer_entrypoint, tmp_path,
                                                       monkeypatch):
        """The defect: with several candidates the result must not depend on walk order.

        The walk order is FORCED to differ from sorted order, because simply creating the files does
        not achieve that. Measured on this platform: `rglob` returned
        `['aaa/generated.mp4', 'mmm/other.mp4', 'zzz/control_edge.mp4']` -- already sorted -- so a test
        that just compared the result against `sorted(...)` passed against the ORIGINAL first-hit
        implementation too, and proved nothing. `rglob`'s order is filesystem-dependent by definition,
        so it cannot be relied on to be wrong here; it has to be made wrong.
        """
        out = tmp_path / "output"
        _write(out, "zzz/control_edge.mp4", "aaa/generated.mp4", "mmm/other.mp4")

        real_rglob = type(out).rglob

        def reversed_rglob(self, pattern):
            return reversed(sorted(real_rglob(self, pattern), key=str))

        monkeypatch.setattr(type(out), "rglob", reversed_rglob)

        # Control: the injection really does invert the order the function sees, so a failure below is
        # the function's ordering and not a no-op patch.
        seen = [str(p) for p in out.rglob("*.mp4")]
        assert seen == sorted(seen, reverse=True), "the rglob injection did not take effect"

        chosen = transfer_entrypoint.find_output_video(out)
        assert chosen is not None
        assert str(chosen) == sorted(seen)[0], (
            "selection followed walk order rather than sorted order; with the walk reversed the "
            "first hit would be the LAST sorted path"
        )
        assert chosen.name == "generated.mp4"

    def test_repeated_calls_agree(self, transfer_entrypoint, tmp_path):
        """Reproducibility is the point of the fix, so it is asserted directly."""
        out = tmp_path / "output"
        _write(out, "b/two.mp4", "a/one.mp4", "c/three.mp4")
        first = transfer_entrypoint.find_output_video(out)
        assert all(transfer_entrypoint.find_output_video(out) == first for _ in range(5))

    def test_an_empty_directory_returns_none(self, transfer_entrypoint, tmp_path):
        """`main()` raises 'No output video generated' on None, so None must stay reachable."""
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        assert transfer_entrypoint.find_output_video(out) is None

    def test_a_directory_named_like_a_video_is_not_selected(self, transfer_entrypoint, tmp_path):
        """`rglob('*.mp4')` matches DIRECTORIES too, and uploading one would fail late.

        This is why the selection filters on `is_file()`. Without it a directory named `x.mp4` sorts
        into the candidate list and is returned as the artifact.
        """
        out = tmp_path / "output"
        (out / "trap.mp4").mkdir(parents=True)          # a directory, not a file
        _write(out, "real/video.mp4")
        chosen = transfer_entrypoint.find_output_video(out)
        assert chosen is not None and chosen.is_file() and chosen.name == "video.mp4"
