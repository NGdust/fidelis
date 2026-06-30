"""Pluggable spec storage.

By default fidelis keeps specs as YAML files next to your project
(``FileSpecStore``). In production you may want them in S3, a database, or a
config service instead — implement :class:`SpecStore` and pass it to the
``Parser`` as ``spec_store=...``.

A spec's identity is its ``signature`` (a short hash of the normalized
source field names). That maps cleanly onto a storage key: ``get(signature)`` is
one object GET / one ``SELECT`` by primary key — no scanning. Only drift
detection needs to look across specs, which is what :meth:`SpecStore.all`
provides; a backend where listing everything is impractical can override
:meth:`SpecStore.find_drift_candidate` (or leave it returning ``None`` to skip
drift detection).

Example — specs in S3, keyed by signature::

    import boto3
    from fidelis import Parser, SpecStore, Spec

    class S3SpecStore(SpecStore):
        def __init__(self, bucket, prefix=""):
            self.s3, self.bucket, self.prefix = boto3.client("s3"), bucket, prefix

        def _key(self, signature):
            return f"{self.prefix}spec_{signature}.yaml"

        def get(self, signature):
            try:
                obj = self.s3.get_object(Bucket=self.bucket, Key=self._key(signature))
            except self.s3.exceptions.NoSuchKey:
                return None
            return Spec.from_yaml(obj["Body"].read().decode("utf-8"))

        def save(self, spec):
            self.s3.put_object(Bucket=self.bucket, Key=self._key(spec.signature),
                               Body=spec.dump_yaml().encode("utf-8"))

        def all(self):  # only needed for drift detection
            pages = self.s3.get_paginator("list_objects_v2")
            for page in pages.paginate(Bucket=self.bucket, Prefix=self.prefix):
                for obj in page.get("Contents", []):
                    body = self.s3.get_object(Bucket=self.bucket, Key=obj["Key"])
                    yield Spec.from_yaml(body["Body"].read().decode("utf-8"))

    parser = Parser(User, spec_store=S3SpecStore("my-bucket", "fidelis/specs/"),
                    llm="anthropic:claude-opus-4-8")
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Iterable, Optional

from .spec import (
    Spec,
    best_drift_candidate,
    find_spec_by_signature,
    iter_specs,
)


class SpecStore(ABC):
    """Where specs are read from and written to.

    Implement :meth:`get` and :meth:`save`. Implement :meth:`all` too if you want
    schema-drift detection (the default :meth:`find_drift_candidate` uses it).
    """

    @abstractmethod
    def get(self, signature: str) -> Optional[Spec]:
        """Return the spec whose ``signature`` equals ``signature``, or None."""

    @abstractmethod
    def save(self, spec: Spec) -> None:
        """Persist ``spec`` (keyed by its ``signature``)."""

    def all(self) -> Iterable[Spec]:
        """All stored specs — used for drift detection. Default: none."""

        return ()

    def find_drift_candidate(
        self, field_names: Iterable[str], *, min_similarity: float = 0.5
    ) -> Optional[Spec]:
        """The known spec most similar to ``field_names`` (for drift), or None."""

        return best_drift_candidate(
            self.all(), field_names, min_similarity=min_similarity
        )


class FileSpecStore(SpecStore):
    """Default store: YAML files in a directory (the historical behavior)."""

    def __init__(self, spec_dir: str | os.PathLike = "specs/"):
        self.spec_dir = str(spec_dir)

    def get(self, signature: str) -> Optional[Spec]:
        return find_spec_by_signature(self.spec_dir, signature)

    def save(self, spec: Spec) -> None:
        spec.save(self.spec_dir)

    def all(self) -> Iterable[Spec]:
        return iter_specs(self.spec_dir)

    def find_drift_candidate(
        self, field_names: Iterable[str], *, min_similarity: float = 0.5
    ) -> Optional[Spec]:
        return best_drift_candidate(
            iter_specs(self.spec_dir), field_names, min_similarity=min_similarity
        )


class MemorySpecStore(SpecStore):
    """In-memory store, keyed by signature — handy for tests and embedding."""

    def __init__(self, specs: Iterable[Spec] = ()):
        self._by_sig: dict[str, Spec] = {s.signature: s for s in specs}

    def get(self, signature: str) -> Optional[Spec]:
        return self._by_sig.get(signature)

    def save(self, spec: Spec) -> None:
        self._by_sig[spec.signature] = spec

    def all(self) -> Iterable[Spec]:
        return list(self._by_sig.values())
