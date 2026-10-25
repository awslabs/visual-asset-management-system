# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The bundled CloudFront origin-facing snapshot is well formed and is what its generator writes.

``clientIp.py`` reads ``X-Forwarded-For`` only for a TCP peer in this snapshot (or in the list it
refreshes at run time), so the snapshot is the whole answer wherever the refresh cannot run. Two
ways it could quietly stop being one:

* it stops parsing, or a family empties, so every request whose TCP peer is a CloudFront
  origin-facing address is judged by that peer address -- covered by the parse checks below;
* it is edited by hand into a shape the generator no longer produces, so the next regeneration is
  an unreviewable diff -- covered by regenerating it from its own data and comparing the text.

Durable, not temporary: both failures stay writable for as long as the file exists.
"""
import ipaddress
import json
import os
import random
import re

import pytest

from backend.backend.common.auth import cloudFrontOriginFacingRanges as snapshot
from backend.scripts import generateCloudFrontOriginFacingRanges as generator

_SNAPSHOT_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "backend", "common", "auth", "cloudFrontOriginFacingRanges.py",
    )
)
# Every file under this directory is packaged into the Lambda functions.
_LAMBDA_ASSET_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

# The documentation ranges the authorizer tests use as non-CloudFront peers.
DOCUMENTATION_RANGES = ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")


def _snapshot_text():
    with open(_SNAPSHOT_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def _document_from_snapshot(seed=0):
    """An ip-ranges document holding the snapshot's prefixes, shuffled, duplicated and mixed
    with other services, as the published document is."""
    rng = random.Random(seed)
    ipv4 = [
        {"ip_prefix": p, "region": "GLOBAL", "service": snapshot.SERVICE, "network_border_group": "GLOBAL"}
        for p in snapshot.IPV4_PREFIXES
    ]
    ipv4 += [dict(ipv4[0], region="us-east-1")]
    ipv4 += [{"ip_prefix": "198.51.100.0/24", "region": "us-east-1", "service": "EC2"}]
    ipv6 = [
        {"ipv6_prefix": p, "region": "GLOBAL", "service": snapshot.SERVICE, "network_border_group": "GLOBAL"}
        for p in snapshot.IPV6_PREFIXES
    ]
    ipv6 += [{"ipv6_prefix": "2001:db8::/32", "region": "us-east-1", "service": "CLOUDFRONT"}]
    rng.shuffle(ipv4)
    rng.shuffle(ipv6)
    return {
        "syncToken": "0",
        "createDate": snapshot.CREATE_DATE,
        "prefixes": ipv4,
        "ipv6_prefixes": ipv6,
    }


@pytest.mark.unit
class TestSnapshotParses:
    def test_names_its_source_and_creation_date(self):
        assert snapshot.SOURCE_URL == "https://ip-ranges.amazonaws.com/ip-ranges.json"
        assert snapshot.SERVICE == "CLOUDFRONT_ORIGIN_FACING"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}", snapshot.CREATE_DATE)
        header = _snapshot_text().split('"""', 2)[1]
        assert snapshot.SOURCE_URL in header
        assert f"createDate: {snapshot.CREATE_DATE}" in header

    @pytest.mark.parametrize(
        "prefixes, network_type",
        [
            (snapshot.IPV4_PREFIXES, ipaddress.IPv4Network),
            (snapshot.IPV6_PREFIXES, ipaddress.IPv6Network),
        ],
        ids=["ipv4", "ipv6"],
    )
    def test_each_family_is_non_empty_strict_unique_and_sorted(self, prefixes, network_type):
        assert prefixes, "a family of the snapshot is empty"
        networks = [network_type(p) for p in prefixes]  # strict: a host bit set raises
        assert len(set(networks)) == len(networks), "a prefix is listed twice"
        assert networks == sorted(networks)
        assert [str(n) for n in networks] == list(prefixes), "a prefix is not in canonical form"

    def test_the_documentation_ranges_used_as_direct_peers_are_outside_it(self):
        listed = [ipaddress.ip_network(p) for p in snapshot.IPV4_PREFIXES + snapshot.IPV6_PREFIXES]
        assert listed, "nothing to compare against"
        for documentation in map(ipaddress.ip_network, DOCUMENTATION_RANGES):
            assert not any(documentation.overlaps(n) for n in listed if n.version == documentation.version)


@pytest.mark.unit
class TestGeneratorReproducesTheSnapshot:
    @pytest.mark.parametrize("seed", [0, 1, 2])
    def test_rendering_the_snapshot_data_yields_the_committed_file(self, seed):
        create_date, ipv4, ipv6 = generator.extract_origin_facing(_document_from_snapshot(seed))
        assert create_date == snapshot.CREATE_DATE
        assert generator.render_module(create_date, ipv4, ipv6) == _snapshot_text()

    def test_the_command_line_writes_the_committed_file(self, tmp_path):
        source = tmp_path / "ip-ranges.json"
        source.write_text(json.dumps(_document_from_snapshot()), encoding="utf-8")
        output = tmp_path / "out.py"
        assert generator.main(["--input", str(source), "--output", str(output)]) == 0
        assert output.read_text(encoding="utf-8") == _snapshot_text()

    def test_the_default_output_is_the_committed_snapshot(self):
        assert os.path.abspath(generator.DEFAULT_OUTPUT) == _SNAPSHOT_PATH

    def test_the_generator_is_not_packaged_into_the_lambda_functions(self):
        script = os.path.abspath(generator.__file__)
        assert os.path.commonpath([script, _LAMBDA_ASSET_ROOT]) != _LAMBDA_ASSET_ROOT
        assert not os.path.exists(os.path.join(os.path.dirname(_SNAPSHOT_PATH), os.path.basename(script)))


@pytest.mark.unit
class TestGeneratorFailsLoudly:
    def test_a_document_without_a_create_date_is_refused(self):
        document = _document_from_snapshot()
        del document["createDate"]
        with pytest.raises(ValueError):
            generator.extract_origin_facing(document)

    @pytest.mark.parametrize("family", ["prefixes", "ipv6_prefixes"])
    def test_a_family_with_no_origin_facing_prefix_is_refused(self, family):
        document = _document_from_snapshot()
        document[family] = [e for e in document[family] if e.get("service") != snapshot.SERVICE]
        with pytest.raises(ValueError):
            generator.extract_origin_facing(document)

    def test_a_prefix_that_does_not_parse_is_refused(self):
        document = _document_from_snapshot()
        document["prefixes"].append({"ip_prefix": "300.0.0.0/8", "service": snapshot.SERVICE})
        with pytest.raises(ValueError):
            generator.extract_origin_facing(document)
