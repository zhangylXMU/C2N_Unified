"""Validate the C2N contracts and their example payloads.

Run from anywhere:

    python docs/contracts/validate_contracts.py

Exit code 0 means: every schema is a valid JSON Schema 2020-12 document, every
cross-file ``$ref`` resolves, and every example instance validates against its
schema.  This is meant to be the first CI gate for the contracts package: the
JSON Schema files are the single source of truth, so they must be executable,
not decorative.
"""

from __future__ import annotations

import json
import pathlib
import sys

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

HERE = pathlib.Path(__file__).resolve().parent
BASE_URI = "https://c2n.dev/schemas/1.0/"

SCHEMA_FILES = (
    "common.schema.json",
    "case-spec.schema.json",
    "technical-result.schema.json",
    "economics.schema.json",
)

#: (schema to validate against, example instance to validate)
#:
#: A target may be a schema file in this directory, or an absolute ``$id`` +
#: fragment.  The fragment form is wrapped in ``{"$ref": ...}`` so that the
#: subschema is resolved *inside* its document; resolving an extracted fragment
#: on its own would make its internal ``#/$defs/...`` pointers dangle.
CASES = (
    ("case-spec.schema.json", "examples/case-spec.demo.json"),
    ("technical-result.schema.json", "examples/technical-result.demo.json"),
    (
        BASE_URI + "economics.schema.json#/$defs/EconomicsInput",
        "examples/economics-input.demo.json",
    ),
)


def load(relative: str) -> dict:
    path = HERE / relative
    return json.loads(path.read_text(encoding="utf-8"))


def build_registry() -> tuple[Registry, dict[str, dict]]:
    registry = Registry()
    documents: dict[str, dict] = {}
    for name in SCHEMA_FILES:
        document = load(name)
        Draft202012Validator.check_schema(document)
        documents[name] = document
        registry = registry.with_resource(
            document["$id"], Resource.from_contents(document)
        )
    return registry, documents


def resolve_target(target: str, documents: dict[str, dict]) -> dict:
    """Return a schema dict for a file name or an absolute ``$id`` + fragment."""

    if target.startswith("http://") or target.startswith("https://"):
        return {"$ref": target}
    return documents[target]


def main() -> int:
    registry, documents = build_registry()
    print(f"schemas: {len(documents)} valid JSON Schema 2020-12 documents")

    failures = 0
    for target, example in CASES:
        schema = resolve_target(target, documents)
        instance = load(example)
        validator = Draft202012Validator(schema, registry=registry)
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
        if errors:
            failures += len(errors)
            print(f"FAIL  {example}  vs  {target}")
            for error in errors:
                where = "/".join(str(p) for p in error.path) or "<root>"
                print(f"      {where}: {error.message}")
        else:
            print(f"ok    {example}")

    if failures:
        print(f"\n{failures} validation error(s)")
        return 1
    print("\nall contracts and examples validate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
