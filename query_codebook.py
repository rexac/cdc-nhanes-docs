#!/usr/bin/env python3
"""Fast local search utility for mirrored NHANES codebooks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from bs4 import BeautifulSoup

from nhanes_core import DEFAULT_ROOT, canonical_component, resolve_cycle_dir


def iter_docs(root: Path, cycle: str | None = None, component: str | None = None):
    base = root
    if cycle:
        base = base / resolve_cycle_dir(root, cycle)
    if component:
        base = base / canonical_component(component)
    if not base.exists():
        return
    yield from sorted(p for p in base.rglob("*.htm") if p.is_file())


def variable_pattern(name: str, exact: bool) -> re.Pattern[str]:
    escaped = re.escape(name)
    if exact:
        return re.compile(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])", re.I)
    return re.compile(escaped, re.I)


def search_docs(
    root: Path,
    term: str,
    *,
    exact: bool = False,
    cycle: str | None = None,
    component: str | None = None,
    limit: int | None = 30,
) -> list[Path]:
    pattern = variable_pattern(term, exact)
    hits: list[Path] = []
    for path in iter_docs(root, cycle, component) or ():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if pattern.search(text):
            hits.append(path)
            if limit and len(hits) >= limit:
                break
    return hits


def html_to_text(source: str) -> str:
    soup = BeautifulSoup(source, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)


def snippet(path: Path, term: str, radius: int = 450) -> str:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return f"[read error: {exc}]"
    text = html_to_text(source)
    match = variable_pattern(term, True).search(text) or re.search(re.escape(term), text, re.I)
    if not match:
        return text[: radius * 2]
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return text[start:end]


def relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search local CDC NHANES codebook HTML files")
    parser.add_argument("term", nargs="?", help="variable name or text to search")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="codebook mirror root")
    parser.add_argument("--exact", action="store_true", help="match a complete NHANES variable token")
    parser.add_argument(
        "--cycle",
        help="restrict to a release; a unique future range can be resolved from its begin year",
    )
    parser.add_argument("--component", help="restrict to a component directory")
    parser.add_argument("--limit", type=int, default=30, help="maximum search results (0 = unlimited)")
    parser.add_argument("--show", action="store_true", help="show a text snippet for each search hit")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--list",
        nargs=2,
        metavar=("CYCLE", "COMPONENT"),
        dest="list_scope",
        help="list codebooks in a cycle/component (legacy and unique future begin-year aliases work)",
    )
    parser.add_argument(
        "--var",
        nargs=2,
        metavar=("VARIABLE", "FILE"),
        help="show the text around VARIABLE in one specific codebook file",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = Path(args.root).expanduser().resolve()

    if args.list_scope:
        cycle, component = args.list_scope
        resolved_cycle = resolve_cycle_dir(root, cycle)
        resolved_component = canonical_component(component)
        docs = list(iter_docs(root, cycle, component) or ())
        if args.json:
            print(json.dumps([relative(p, root) for p in docs], ensure_ascii=False, indent=2))
        else:
            if not docs:
                print(f"No directory/documents: {resolved_cycle}/{resolved_component}")
            for path in docs:
                print(relative(path, root))
        return

    if args.var:
        variable, file_name = args.var
        path = Path(file_name).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if not path.is_file():
            print(f"File not found: {path}", file=sys.stderr)
            sys.exit(2)
        print(snippet(path, variable))
        return

    if not args.term:
        build_parser().print_help()
        sys.exit(2)

    limit = None if args.limit == 0 else max(1, args.limit)
    hits = search_docs(
        root,
        args.term,
        exact=args.exact,
        cycle=args.cycle,
        component=args.component,
        limit=limit,
    )

    if args.json:
        payload = [
            {
                "path": relative(path, root),
                **({"snippet": snippet(path, args.term)} if args.show else {}),
            }
            for path in hits
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"Search [{args.term}] exact={args.exact}: {len(hits)} hit(s)")
    for path in hits:
        print(f"  {relative(path, root)}")
        if args.show:
            print(f"    {snippet(path, args.term)}")
    if not hits:
        sys.exit(1)


if __name__ == "__main__":
    main()
