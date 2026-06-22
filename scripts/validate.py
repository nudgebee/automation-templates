#!/usr/bin/env python3
"""Validate every template file against the JSON schema and repo conventions.
Run by CI on every PR (see .github/workflows/validate.yaml)."""
import glob
import os
import sys

import jsonschema
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "schema", "template.schema.json")
TEMPLATES_GLOB = os.path.join(ROOT, "templates", "*.yaml")


def main():
    import json

    schema = json.load(open(SCHEMA_PATH))
    files = sorted(glob.glob(TEMPLATES_GLOB))
    if not files:
        print("no templates found", file=sys.stderr)
        sys.exit(1)

    errors = 0
    slugs = set()
    for path in files:
        name = os.path.basename(path)
        try:
            doc = yaml.safe_load(open(path, "rb").read())
        except yaml.YAMLError as e:
            print(f"::error file={path}::invalid YAML: {e}")
            errors += 1
            continue
        try:
            jsonschema.validate(doc, schema)
        except jsonschema.ValidationError as e:
            print(f"::error file={path}::schema validation failed: {e.message}")
            errors += 1
            continue
        stem = os.path.splitext(name)[0]
        if doc.get("slug") != stem:
            print(f"::error file={path}::slug {doc.get('slug')!r} must equal file name {stem!r}")
            errors += 1
        if doc.get("slug") in slugs:
            print(f"::error file={path}::duplicate slug {doc.get('slug')!r}")
            errors += 1
        slugs.add(doc.get("slug"))

    if errors:
        print(f"\n{errors} validation error(s).", file=sys.stderr)
        sys.exit(1)
    print(f"OK: {len(files)} template(s) valid.")


if __name__ == "__main__":
    main()
