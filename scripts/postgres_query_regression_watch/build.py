"""Build templates/postgres_query_regression_watch.yaml from template.yaml.in and pqf.py.

The template's script step embeds pqf.py, so pqf.py and its tests stay the source of truth.
  python3 scripts/postgres_query_regression_watch/build.py          # write the template
  python3 scripts/postgres_query_regression_watch/build.py --check  # fail if it is stale
"""
import pathlib
import sys

here = pathlib.Path(__file__).parent
dest = here.parent.parent / "templates" / "postgres_query_regression_watch.yaml"


def build():
    script = (here / "pqf.py").read_text()
    for bad in ("{{", "{%", "{#"):
        if bad in script:
            sys.exit(f"pqf.py contains {bad!r}, which the workflow template engine would render")
    indented = "".join("        " + line if line.strip() else "\n" for line in script.splitlines(True))
    head = (here / "template.yaml.in").read_text()
    if "        __PQF_SCRIPT__\n" not in head:
        sys.exit("template.yaml.in has no __PQF_SCRIPT__ placeholder")
    return head.replace("        __PQF_SCRIPT__\n", indented)


if __name__ == "__main__":
    out = build()
    if "--check" in sys.argv[1:]:
        if not dest.exists() or dest.read_text() != out:
            sys.exit(f"{dest} is stale: run python3 {pathlib.Path(__file__).as_posix()}")
        print(f"{dest.name} is up to date")
    else:
        dest.write_text(out)
        print(f"wrote {dest}")
