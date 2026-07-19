#!/usr/bin/env python3
"""
Build the shipped app by injecting the data blobs into the template.

Reads:   _build_sources/app_template.html   (source of truth for all code/CSS/HTML)
         _build_sources/seed.json           -> replaces __SEED__      (line 307)
         _build_sources/minnames.json        -> replaces __MINNAMES__  (line 308)
         _build_sources/user.txt (optional)  -> replaces __USER__      (line 309)

Writes:  2026 Collection Database.html       (the double-click deliverable)

Usage:   python3 _build_sources/build.py
The output differs from the template only in the three data lines (307-309).
Never hand-edit the shipped file's logic — edit the template and re-run this.
"""
import base64
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent          # _build_sources/
ROOT = HERE.parent                                       # project root

TEMPLATE = HERE / "app_template.html"
SEED = HERE / "seed.json"
MINNAMES = HERE / "minnames.json"
USER_FILE = HERE / "user.txt"
OUT = ROOT / "demo.html"

# Official letterhead seals, embedded as data URIs so the app stays fully offline.
LOGOS = {
    "__LOGO_MINISTRY__": HERE / "logo_ministry.png",
    "__LOGO_OFFICE__": HERE / "logo_office.png",
    "__LOGO_EMBLEM__": HERE / "logo_emblem.png",
}

DEFAULT_USER = "demo@example.com"


def read(p):
    return p.read_text(encoding="utf-8")


def data_uri(p):
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")


def main():
    tpl = read(TEMPLATE)
    seed = read(SEED).rstrip("\n")
    minnames = read(MINNAMES).rstrip("\n")
    user = read(USER_FILE).strip() if USER_FILE.exists() else DEFAULT_USER

    for token, name in (("__SEED__", "seed.json"),
                        ("__MINNAMES__", "minnames.json"),
                        ("__USER__", "user")):
        if token not in tpl:
            sys.exit(f"ERROR: placeholder {token} not found in template "
                     f"(cannot inject {name}).")

    out = (tpl
           .replace("__SEED__", seed)
           .replace("__MINNAMES__", minnames)
           .replace("__USER__", user))

    for token, path in LOGOS.items():
        if token in out:
            if not path.exists():
                sys.exit(f"ERROR: {path.name} missing (needed for {token}).")
            out = out.replace(token, data_uri(path))

    dest = sys.argv[1] if len(sys.argv) > 1 else str(OUT)
    pathlib.Path(dest).write_text(out, encoding="utf-8")
    print(f"Built {dest} ({len(out):,} bytes) from {TEMPLATE.name}.")


if __name__ == "__main__":
    main()
