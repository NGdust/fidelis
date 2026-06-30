"""Tests for fidelis/infer_model.py and the `fidelis infer-model` CLI command.

Covers identifier normalization, per-column type inference, optionality, the
rendered source (imports + class body), that the output actually imports and
validates data, and the CLI command (stdout + --out).
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel

import fidelis as fp
from fidelis import cli
from fidelis.infer_model import (
    InferredField,
    _infer_type,
    _to_identifier,
    infer_model_source,
    render_model,
)


# --------------------------------------------------------------------------- #
# _to_identifier
# --------------------------------------------------------------------------- #


def test_identifier_snake_cases_and_lowercases():
    taken: set[str] = set()
    assert _to_identifier("E-mail", taken) == "e_mail"
    assert _to_identifier("Full Name", taken) == "full_name"


def test_identifier_handles_leading_digit_and_keyword():
    taken: set[str] = set()
    assert _to_identifier("123abc", taken).startswith("field_")
    assert _to_identifier("class", taken) == "class_"


def test_identifier_dedups_collisions():
    taken: set[str] = set()
    a = _to_identifier("Name", taken)
    b = _to_identifier("name", taken)  # normalizes to the same base
    assert a == "name" and b == "name_2"


# --------------------------------------------------------------------------- #
# _infer_type
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "values,expected",
    [
        (["1", "2", "3"], "int"),
        (["1.5", "2,5"], "float"),
        (["yes", "no", "true"], "bool"),
        (["2026-06-28", "2026-01-01"], "date"),
        (["28.06.2026"], "date"),
        (["hello", "world"], "str"),
        (["1", "0"], "int"),  # not swallowed as bool
        ([], "str"),
        (["", None], "str"),  # all empty -> str
    ],
)
def test_infer_type(values, expected):
    assert _infer_type(values) == expected


# --------------------------------------------------------------------------- #
# render_model
# --------------------------------------------------------------------------- #


def test_render_includes_conditional_imports():
    fields = [
        InferredField("email", "str", False, "email"),
        InferredField("when", "date", True, "When"),
    ]
    src = render_model("User", fields)
    assert "from datetime import date" in src
    assert "from typing import Optional" in src
    assert "class User(BaseModel):" in src
    assert "when: Optional[date] = None  # from 'When'" in src
    assert "email: str" in src


def test_render_no_optional_no_date_omits_imports():
    fields = [InferredField("n", "int", False, "n")]
    src = render_model("M", fields)
    assert "from typing import Optional" not in src
    assert "from datetime import date" not in src


def test_rendered_model_imports_and_validates(tmp_path):
    fields = [
        InferredField("email", "str", False, "E-mail"),
        InferredField("age", "int", False, "Age"),
        InferredField("signup", "date", True, "Signup"),
    ]
    src = render_model("Person", fields)

    # Write and import it like a real generated module (gives pydantic a proper
    # module namespace to resolve annotations against).
    import importlib.util

    mod_path = tmp_path / "inferred_person.py"
    mod_path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("inferred_person", mod_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    Person = module.Person

    assert issubclass(Person, BaseModel)
    obj = Person(email="a@b.com", age="42", signup="2026-06-28")
    assert obj.age == 42 and obj.signup == date(2026, 6, 28)
    # Optional field can be omitted.
    assert Person(email="x", age=1).signup is None


# --------------------------------------------------------------------------- #
# infer_model_source end-to-end
# --------------------------------------------------------------------------- #


def test_infer_from_records():
    records = [
        {"E-mail": "a@b.com", "Age": "30", "Joined": "2026-01-01"},
        {"E-mail": "b@c.com", "Age": "25", "Joined": "2026-02-01"},
    ]
    src = infer_model_source(records, class_name="User")
    assert "class User(BaseModel):" in src
    assert "e_mail: str" in src
    assert "age: int" in src
    assert "joined: date" in src


def test_infer_marks_optional_on_missing_value():
    records = [
        {"name": "Alice", "nickname": "Al"},
        {"name": "Bob", "nickname": ""},  # empty -> nickname optional
    ]
    src = infer_model_source(records, class_name="C")
    assert "nickname: Optional[str] = None" in src
    assert "name: str" in src


def test_infer_from_csv_string():
    csv_text = "E-mail;Age\na@b.com;30\nb@c.com;25\n"
    src = infer_model_source(csv_text, class_name="U", kind="csv")
    assert "age: int" in src


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_infer_model_stdout(tmp_path, capsys):
    src = tmp_path / "feed.csv"
    src.write_text("E-mail;Age\na@b.com;30\n", encoding="utf-8")
    code = cli.main(["infer-model", str(src), "--name", "User"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "class User(BaseModel):" in out
    assert "age: int" in out


def test_cli_infer_model_out_file(tmp_path, capsys):
    src = tmp_path / "feed.csv"
    src.write_text("Name\nAlice\n", encoding="utf-8")
    out = tmp_path / "model.py"
    code = cli.main(["infer-model", str(src), "--name", "C", "--out", str(out)])
    assert code == cli.EXIT_OK
    assert "class C(BaseModel):" in out.read_text(encoding="utf-8")
    assert "wrote" in capsys.readouterr().err
