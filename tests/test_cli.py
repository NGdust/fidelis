"""Tests for the fidelis command-line interface (fidelis/cli.py).

Each test drives ``cli.main([...])`` directly and asserts on the returned exit
code plus captured stdout/stderr. The target model is referenced as
``conftest:User`` / ``conftest:Product`` (pytest puts the tests dir on sys.path).
"""

from __future__ import annotations

import json
from datetime import date

import pytest

import fidelis as fp
from fidelis import cli
from fidelis.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE

from conftest import USER_MAPPINGS, User


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


def _user_spec(spec_dir):
    return fp.Spec(
        signature=fp.compute_signature(["E-mail", "Name", "Date"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="full_name", source="Name", transform="strip"),
            fp.Mapping(
                target="signup_date", source="Date", transform="parse_date:%d.%m.%Y"
            ),
        ],
    ).save(spec_dir)


def _csv(tmp_path, text="E-mail;Name;Date\na@b.com;Alice;01.02.2026\nbob@x.com;Bob;15.03.2026\n"):
    path = tmp_path / "feed.csv"
    path.write_text(text, encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------- #
# _load_model
# --------------------------------------------------------------------------- #


def test_load_model_resolves_module_class():
    assert cli._load_model("conftest:User") is User


def test_load_model_rejects_dotted_only():
    with pytest.raises(cli.CLIError):
        cli._load_model("conftest.User")


def test_load_model_unknown_attribute():
    with pytest.raises(cli.CLIError):
        cli._load_model("conftest:Nope")


def test_load_model_not_a_basemodel():
    with pytest.raises(cli.CLIError):
        cli._load_model("conftest:USER_MAPPINGS")  # a list, not a model


# --------------------------------------------------------------------------- #
# version / usage
# --------------------------------------------------------------------------- #


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "fidelis" in capsys.readouterr().out


def test_bad_model_ref_exits_usage(tmp_path, capsys):
    code = cli.main(["parse", _csv(tmp_path), "--model", "conftest.User", "--spec-dir", str(tmp_path)])
    assert code == EXIT_USAGE
    assert "error:" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# parse
# --------------------------------------------------------------------------- #


def test_parse_clean_feed_exits_ok(tmp_path, spec_dir, capsys):
    _user_spec(spec_dir)
    code = cli.main(["parse", _csv(tmp_path), "--model", "conftest:User", "--spec-dir", str(spec_dir)])
    assert code == EXIT_OK
    assert "valid=2 errors=0" in capsys.readouterr().err


def test_parse_with_errors_exits_findings_and_writes_csv(tmp_path, spec_dir, capsys):
    _user_spec(spec_dir)
    # Second row has an unparseable date -> one error row.
    src = _csv(tmp_path, "E-mail;Name;Date\na@b.com;Alice;01.02.2026\nbob@x.com;Bob;not-a-date\n")
    errors_out = tmp_path / "errs.csv"
    code = cli.main(
        ["parse", src, "--model", "conftest:User", "--spec-dir", str(spec_dir), "--errors", str(errors_out)]
    )
    assert code == EXIT_FINDINGS
    body = errors_out.read_text(encoding="utf-8")
    assert "row_index,field,reason,raw_value" in body
    assert "signup_date" in body


def test_parse_json_report(tmp_path, spec_dir, capsys):
    _user_spec(spec_dir)
    code = cli.main(["parse", _csv(tmp_path), "--model", "conftest:User", "--spec-dir", str(spec_dir), "--json"])
    out = capsys.readouterr().out
    report = json.loads(out)
    assert code == EXIT_OK
    assert report["valid"] == 2 and report["errors"] == 0


def test_parse_out_writes_valid_rows_json(tmp_path, spec_dir):
    _user_spec(spec_dir)
    out = tmp_path / "rows.json"
    code = cli.main(["parse", _csv(tmp_path), "--model", "conftest:User", "--spec-dir", str(spec_dir), "--out", str(out)])
    assert code == EXIT_OK
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert rows[0]["email"] == "a@b.com"
    assert rows[0]["signup_date"] == "2026-02-01"


def test_parse_no_spec_no_llm_exits_usage(tmp_path, spec_dir, capsys):
    # Empty spec dir + no --llm -> SpecNotFoundError surfaces as usage error.
    code = cli.main(["parse", _csv(tmp_path), "--model", "conftest:User", "--spec-dir", str(spec_dir)])
    assert code == EXIT_USAGE
    assert "error:" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# generate-spec
# --------------------------------------------------------------------------- #


def test_generate_spec_with_fake_llm_writes_yaml(tmp_path, spec_dir, capsys):
    src = _csv(tmp_path)
    # The 'fake' provider needs a model segment (provider:model). With no canned
    # mappings it returns an empty mapping set — enough to assert a spec file is
    # written without any network call.
    code = cli.main(["generate-spec", src, "--model", "conftest:User", "--spec-dir", str(spec_dir), "--llm", "fake:test"])
    assert code == EXIT_OK
    written = list(spec_dir.glob("*.yaml"))
    assert len(written) == 1
    assert "wrote" in capsys.readouterr().err


def test_generate_spec_without_llm_exits_usage(tmp_path, spec_dir, capsys):
    code = cli.main(["generate-spec", _csv(tmp_path), "--model", "conftest:User", "--spec-dir", str(spec_dir)])
    assert code == EXIT_USAGE
    assert "error:" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# validate-spec
# --------------------------------------------------------------------------- #


def test_validate_spec_ok(tmp_path, spec_dir, capsys):
    spec_path = str(_user_spec(spec_dir))
    code = cli.main(["validate-spec", spec_path, "--model", "conftest:User", "--spec-dir", str(spec_dir)])
    assert code == EXIT_OK
    assert "ok" in capsys.readouterr().err


def test_validate_spec_flags_problems(tmp_path, spec_dir, capsys):
    # A spec missing the required signup_date mapping -> problem reported.
    bad = fp.Spec(
        signature=fp.compute_signature(["E-mail", "Name"]),
        mappings=[fp.Mapping(target="email", source="E-mail", transform="strip_lower")],
    ).save(spec_dir)
    code = cli.main(["validate-spec", str(bad), "--model", "conftest:User", "--spec-dir", str(spec_dir)])
    assert code == EXIT_FINDINGS
    assert "problem" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# check-drift
# --------------------------------------------------------------------------- #


def test_check_drift_exact_match_ok(tmp_path, spec_dir, capsys):
    _user_spec(spec_dir)
    code = cli.main(["check-drift", _csv(tmp_path), "--model", "conftest:User", "--spec-dir", str(spec_dir)])
    assert code == EXIT_OK
    assert "no drift" in capsys.readouterr().err


def test_check_drift_detects_new_column(tmp_path, spec_dir, capsys):
    _user_spec(spec_dir)
    # Same source plus an extra column -> not an exact match, near-match drift.
    src = _csv(tmp_path, "E-mail;Name;Date;Phone\na@b.com;Alice;01.02.2026;123\n")
    code = cli.main(["check-drift", src, "--model", "conftest:User", "--spec-dir", str(spec_dir)])
    assert code == EXIT_FINDINGS
    err = capsys.readouterr().err
    assert "drift" in err.lower()


def test_check_drift_no_spec_at_all(tmp_path, spec_dir, capsys):
    code = cli.main(["check-drift", _csv(tmp_path), "--model", "conftest:User", "--spec-dir", str(spec_dir)])
    assert code == EXIT_FINDINGS
    assert "no cached spec" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# transforms
# --------------------------------------------------------------------------- #


def test_transforms_lists_builtins(capsys):
    code = cli.main(["transforms"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    for name in ("strip", "to_int", "parse_date"):
        assert name in out


# --------------------------------------------------------------------------- #
# --import (load custom registrations)
# --------------------------------------------------------------------------- #


def test_import_flag_loads_registrations(spec_dir, tmp_path, monkeypatch):
    # A spec referencing a custom enrichment that lives in a module on disk.
    spec_path = str(fp.Spec(
        signature=fp.compute_signature(["E-mail", "Name", "Date"]),
        mappings=[
            fp.Mapping(target="email", source="E-mail", transform="strip_lower"),
            fp.Mapping(target="full_name", source="Name", transform="strip"),
            fp.Mapping(target="signup_date", source="Date", transform="parse_date:%d.%m.%Y"),
        ],
        enrich=["noop_enrich"],
    ).save(spec_dir))

    mod = tmp_path / "myhooks.py"
    mod.write_text(
        "import fidelis\n"
        "fidelis.register_enrichment('noop_enrich', lambda r, s: r, overwrite=True)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    # Without --import the enrichment name is unknown.
    bad = cli.main(["validate-spec", spec_path, "--model", "conftest:User", "--spec-dir", str(spec_dir)])
    assert bad == EXIT_FINDINGS

    # With --import the registration is loaded and the spec validates.
    ok = cli.main(["validate-spec", spec_path, "--model", "conftest:User", "--spec-dir", str(spec_dir), "--import", "myhooks"])
    assert ok == EXIT_OK


def test_import_unknown_module_exits_usage(tmp_path, spec_dir, capsys):
    _user_spec(spec_dir)
    code = cli.main(["check-drift", _csv(tmp_path), "--model", "conftest:User", "--spec-dir", str(spec_dir), "--import", "no_such_module_xyz"])
    assert code == EXIT_USAGE
    assert "could not import" in capsys.readouterr().err
