"""Command-line interface for fidelis.

Thin wrapper over the public :class:`~fidelis.Parser` API so the same
deterministic ingest can run from a shell, a Makefile, or CI without writing a
script. Five subcommands:

- ``parse`` — parse a source, report valid/error counts, optionally dump valid
  rows and error rows to files;
- ``generate-spec`` — one-off LLM inference that writes a draft spec for review;
- ``validate-spec`` — lint one or more spec files against the target model;
- ``check-drift`` — compare a source's schema to the cached spec (for CI gates);
- ``transforms`` — list the registered transforms.

Exit codes are stable for scripting:

- ``0`` — success, nothing to flag;
- ``1`` — data-level findings (validation errors, spec problems, drift detected);
- ``2`` — usage/runtime error (bad ``--model`` ref, missing file, no spec/LLM).

The target model is given as ``module:Class`` (e.g. ``app.models:User``); the
current working directory is put on ``sys.path`` so local modules import.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
import sys
from typing import Optional, Sequence, Type

from pydantic import BaseModel

from . import __version__
from .drift import DriftError, detect_drift
from .infer_model import infer_model_source
from .parser import Parser, SpecNotFoundError
from .spec import compute_signature, find_drift_candidate, find_spec_by_signature
from .transforms import available_transforms

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2


class CLIError(Exception):
    """A user-facing error: its message goes to stderr and exits ``EXIT_USAGE``."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_model(ref: str) -> Type[BaseModel]:
    """Resolve a ``module:Class`` reference to a Pydantic model class."""

    if ":" not in ref:
        raise CLIError(
            f"--model must be 'module:Class' (e.g. app.models:User), got {ref!r}"
        )
    mod_name, _, attr = ref.partition(":")
    if not mod_name or not attr:
        raise CLIError(f"--model must be 'module:Class', got {ref!r}")
    try:
        module = importlib.import_module(mod_name)
    except ImportError as exc:
        raise CLIError(f"could not import module {mod_name!r}: {exc}") from exc
    try:
        obj = getattr(module, attr)
    except AttributeError as exc:
        raise CLIError(f"module {mod_name!r} has no attribute {attr!r}") from exc
    if not (isinstance(obj, type) and issubclass(obj, BaseModel)):
        raise CLIError(f"{ref!r} is not a pydantic BaseModel subclass")
    return obj


def _make_parser(args: argparse.Namespace) -> Parser:
    kwargs: dict = {"spec_store": args.spec_dir}
    if getattr(args, "llm", None):
        kwargs["llm"] = args.llm
    if getattr(args, "strict", False):
        kwargs["strict"] = True
    dedup_key = _dedup_key_fields(getattr(args, "dedup_key", None))
    if dedup_key:
        kwargs["dedup_key"] = dedup_key
        kwargs["dedup_keep"] = getattr(args, "dedup_keep", "first")
    if getattr(args, "domain_hints", None):
        kwargs["domain_hints"] = args.domain_hints
    return Parser(_load_model(args.model), **kwargs)


def _dedup_key_fields(raw: Optional[list]) -> list:
    """Flatten repeated/comma-separated --dedup-key values into field names."""

    fields: list[str] = []
    for item in raw or []:
        fields.extend(part.strip() for part in item.split(",") if part.strip())
    return fields


def _eprint(*parts: object) -> None:
    print(*parts, file=sys.stderr)


def _write_error_rows(path: str, errors) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["row_index", "field", "reason", "raw_value"])
        for e in errors:
            writer.writerow([e.row_index, e.field or "", e.reason, repr(e.raw_value)])


def _write_valid_rows(path: str, rows) -> None:
    payload = [r.model_dump(mode="json") for r in rows]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)


# --------------------------------------------------------------------------- #
# Subcommands — each returns an exit code
# --------------------------------------------------------------------------- #


def _cmd_parse(args: argparse.Namespace) -> int:
    parser = _make_parser(args)
    try:
        result = parser.parse(args.source, kind=args.kind)
    except DriftError as exc:
        _eprint("drift:", exc.report.describe())
        return EXIT_FINDINGS

    report = {
        "valid": len(result.valid_rows),
        "errors": len(result.errors),
        "duplicates": len(result.duplicates),
        "coverage": round(result.coverage.score, 4),
        "needs_review": result.needs_review,
        "drift": result.drift_report.has_drift,
        "spec_generated": result.spec_generated,
    }

    if args.errors:
        _write_error_rows(args.errors, result.errors)
    if args.quarantine:
        result.write_quarantine(args.quarantine)
    if args.out:
        _write_valid_rows(args.out, result.valid_rows)

    if args.json:
        report["error_rows"] = [
            {
                "row_index": e.row_index,
                "field": e.field,
                "reason": e.reason,
                "raw_value": repr(e.raw_value),
            }
            for e in result.errors
        ]
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _eprint(result.summary())
        if result.duplicates:
            _eprint(f"  dropped {len(result.duplicates)} duplicate(s)")
        for e in result.errors[: args.show_errors]:
            _eprint("  -", e)
        extra = len(result.errors) - args.show_errors
        if extra > 0:
            _eprint(f"  … and {extra} more (use --errors to dump all)")

    return EXIT_FINDINGS if (result.errors or result.needs_review) else EXIT_OK


def _cmd_generate_spec(args: argparse.Namespace) -> int:
    parser = _make_parser(args)
    try:
        spec = parser.generate_spec(args.source, kind=args.kind)
    except SpecNotFoundError as exc:
        raise CLIError(str(exc)) from exc
    path = os.path.join(args.spec_dir, f"spec_{spec.signature}.yaml")
    _eprint(f"wrote {path}")
    _eprint(
        f"  {len(spec.mappings)} mapping(s); "
        f"{sum(m.status == 'needs_review' for m in spec.mappings)} need review"
    )
    if args.show:
        print(spec.dump_yaml())
    return EXIT_OK


def _cmd_validate_spec(args: argparse.Namespace) -> int:
    parser = _make_parser(args)
    worst = EXIT_OK
    for spec_path in args.specs:
        problems = parser.validate_spec(spec_path)
        if problems:
            worst = EXIT_FINDINGS
            _eprint(f"{spec_path}: {len(problems)} problem(s)")
            for p in problems:
                _eprint(f"  - {p}")
        else:
            _eprint(f"{spec_path}: ok")
    return worst


def _cmd_check_drift(args: argparse.Namespace) -> int:
    parser = _make_parser(args)
    data = parser._load_source(args.source, args.kind)
    signature = compute_signature(data.field_names)

    if find_spec_by_signature(args.spec_dir, signature) is not None:
        _eprint("no drift: source matches a cached spec exactly")
        return EXIT_OK

    candidate = find_drift_candidate(args.spec_dir, data.field_names)
    if candidate is None:
        _eprint(
            "no cached spec matches this source and no near-match to compare "
            "against — run 'fidelis generate-spec' first"
        )
        return EXIT_FINDINGS

    report = detect_drift(candidate, data.field_names)
    _eprint(f"compared against spec {candidate.signature!r}")
    _eprint(report.describe())
    return EXIT_FINDINGS if report.has_drift else EXIT_OK


def _cmd_infer_model(args: argparse.Namespace) -> int:
    source = infer_model_source(
        args.source, class_name=args.name, kind=args.kind, sample_size=args.sample_size
    )
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(source)
        _eprint(f"wrote {args.out}")
    else:
        print(source, end="")
    return EXIT_OK


def _cmd_transforms(_args: argparse.Namespace) -> int:
    for name in available_transforms():
        print(name)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def _add_model_arg(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--model",
        required=True,
        metavar="module:Class",
        help="target Pydantic model, e.g. app.models:User",
    )


def _add_spec_dir_arg(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--spec-dir", default="specs/", help="directory of spec YAMLs (default: specs/)"
    )


def _add_source_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("source", help="path to the feed (CSV/JSON/Excel) or a JSON/CSV string")
    sub.add_argument(
        "--kind",
        choices=["csv", "json", "excel", "records"],
        default=None,
        help="force the source kind (default: infer from extension/type)",
    )


def _add_import_arg(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "--import",
        dest="import_modules",
        action="append",
        metavar="MODULE",
        help="import this module first to register custom transforms/enrichments "
        "(repeatable), e.g. --import app.hooks",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fidelis", description="Deterministic feed ingestion.")
    ap.add_argument("--version", action="version", version=f"fidelis {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    # parse
    p = sub.add_parser("parse", help="parse a source and report results")
    _add_source_args(p)
    _add_model_arg(p)
    _add_spec_dir_arg(p)
    _add_import_arg(p)
    p.add_argument("--llm", default=None, help="provider for spec generation, e.g. anthropic:claude-opus-4-8")
    p.add_argument("--strict", action="store_true", help="fail rows on extra/unknown fields")
    p.add_argument("--errors", metavar="PATH", help="write error rows to a CSV file")
    p.add_argument(
        "--quarantine",
        metavar="PATH",
        help="write rejected rows (original data + reason) to a fixable CSV/JSON file",
    )
    p.add_argument("--out", metavar="PATH", help="write valid rows to a JSON file")
    p.add_argument(
        "--dedup-key",
        action="append",
        metavar="FIELD",
        help="model field(s) forming the row key; repeat or comma-separate for a composite key",
    )
    p.add_argument(
        "--dedup-keep",
        choices=["first", "last"],
        default="first",
        help="which occurrence to keep on a key collision (default: first)",
    )
    p.add_argument("--json", action="store_true", help="print a machine-readable report to stdout")
    p.add_argument("--show-errors", type=int, default=10, metavar="N", help="how many error rows to print (default: 10)")
    p.set_defaults(func=_cmd_parse)

    # generate-spec
    g = sub.add_parser("generate-spec", help="generate and save a draft spec (LLM)")
    _add_source_args(g)
    _add_model_arg(g)
    _add_spec_dir_arg(g)
    _add_import_arg(g)
    g.add_argument("--llm", default=None, help="provider, e.g. anthropic:claude-opus-4-8")
    g.add_argument("--domain-hints", default=None, metavar="TEXT", help="domain context for the LLM (allowed values, units, ranges…)")
    g.add_argument("--show", action="store_true", help="print the generated spec YAML")
    g.set_defaults(func=_cmd_generate_spec)

    # validate-spec
    v = sub.add_parser("validate-spec", help="lint spec files against the model")
    v.add_argument("specs", nargs="+", help="one or more spec YAML paths")
    _add_model_arg(v)
    _add_spec_dir_arg(v)
    _add_import_arg(v)
    v.set_defaults(func=_cmd_validate_spec)

    # check-drift
    d = sub.add_parser("check-drift", help="exit nonzero if the source's schema drifted")
    _add_source_args(d)
    _add_model_arg(d)
    _add_spec_dir_arg(d)
    _add_import_arg(d)
    d.set_defaults(func=_cmd_check_drift)

    # infer-model
    im = sub.add_parser("infer-model", help="infer a draft Pydantic model from a sample")
    _add_source_args(im)
    im.add_argument("--name", default="Model", help="class name for the generated model")
    im.add_argument("--out", metavar="PATH", help="write the model to a .py file (default: stdout)")
    im.add_argument("--sample-size", type=int, default=50, help="rows to sample for inference (default: 50)")
    im.set_defaults(func=_cmd_infer_model)

    # transforms
    t = sub.add_parser("transforms", help="list registered transforms")
    t.set_defaults(func=_cmd_transforms)

    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point. Returns a process exit code."""

    # Make the user's local modules importable (model refs like app.models:User).
    if "" not in sys.path and os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())

    ap = build_arg_parser()
    args = ap.parse_args(argv)
    try:
        _import_user_modules(getattr(args, "import_modules", None))
        return args.func(args)
    except (CLIError, SpecNotFoundError, FileNotFoundError, ValueError) as exc:
        _eprint(f"error: {exc}")
        return EXIT_USAGE


def _import_user_modules(names: Optional[Sequence[str]]) -> None:
    """Import modules named via --import so their registrations take effect."""

    for name in names or []:
        try:
            importlib.import_module(name)
        except ImportError as exc:
            raise CLIError(f"could not import {name!r}: {exc}") from exc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
