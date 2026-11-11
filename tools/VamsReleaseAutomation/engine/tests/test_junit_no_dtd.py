# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""JUnit results are parsed without a DTD: a file that declares one counts as unparseable, never expanded."""

from __future__ import annotations

from vra import tests_runner

OK = '<testsuite name="s" tests="1" failures="0" errors="0" skipped="0"><testcase name="a"/></testsuite>'
BOMB = ('<?xml version="1.0"?><!DOCTYPE t [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        '<testsuite name="&b;" tests="1"><testcase name="x"/></testsuite>')
EXTERNAL = ('<?xml version="1.0"?><!DOCTYPE t [<!ENTITY x SYSTEM "file:///etc/hostname">]>'
            '<testsuite name="&x;" tests="1"><testcase name="x"/></testsuite>')


def test_a_plain_junit_file_is_counted(tmp_path):
    (tmp_path / "a.xml").write_text(OK)
    s = tests_runner.aggregate_junit(tmp_path)
    assert s["tests"] == 1 and s["errors"] == 0


def test_a_junit_file_with_a_dtd_is_an_error_not_expanded(tmp_path):
    (tmp_path / "bomb.xml").write_text(BOMB)
    (tmp_path / "ext.xml").write_text(EXTERNAL)
    s = tests_runner.aggregate_junit(tmp_path)
    assert s["tests"] == 0 and s["errors"] == 2
    assert sorted(s["failed"]) == ["bomb.xml: unparseable JUnit", "ext.xml: unparseable JUnit"]
