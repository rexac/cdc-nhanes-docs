#!/usr/bin/env python3
"""Synchronize and validate a local mirror of CDC NHANES HTML codebooks."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import sys

from nhanes_core import (
    BASE_RELEASES,
    CDCClient,
    CDCError,
    DEFAULT_ROOT,
    MIN_VALID_SIZE,
    Release,
    atomic_write_text,
    looks_like_cycle_dir,
    normalize_html,
)


@dataclass
class Stats:
    expected: int = 0
    local: int = 0
    downloaded: int = 0
    migrated: int = 0
    skipped: int = 0
    missing: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    err404: list[str] = field(default_factory=list)
    tiny: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    prune_blocked: str | None = None


class CDCSync:
    def __init__(
        self,
        root: str | Path,
        *,
        check_only: bool = False,
        prune: bool = False,
        quiet: bool = False,
        verify_content: bool = True,
        include_non_public: bool = False,
        max_prune: int = 100,
        allow_large_prune: bool = False,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.check_only = check_only
        self.prune = prune
        self.quiet = quiet
        self.verify_content = verify_content
        self.include_non_public = include_non_public
        self.max_prune = max_prune
        self.allow_large_prune = allow_large_prune
        self.client = CDCClient()
        self.stats = Stats()
        self.releases: tuple[Release, ...] = BASE_RELEASES
        self.root.mkdir(parents=True, exist_ok=True)

    def log(self, message: str = "") -> None:
        if not self.quiet:
            print(message, flush=True)

    def expected_path(self, cycle_dir: str, component_dir: str, filename: str) -> Path:
        return self.root / cycle_dir / component_dir / filename

    def active_component_keys(self) -> set[tuple[str, str]]:
        """Return local release/component pairs that already contain mirrored HTML."""
        active: set[tuple[str, str]] = set()
        if not self.root.is_dir():
            return active
        for cycle_dir in self.root.iterdir():
            if not cycle_dir.is_dir():
                continue
            for component_dir in cycle_dir.iterdir():
                if component_dir.is_dir() and any(component_dir.rglob("*.htm")):
                    active.add((cycle_dir.name, component_dir.name))
        return active

    def validate_discovered_release_dirs(self) -> None:
        """Abort if a cycle-like local release vanished from CDC discovery.

        This prevents a temporary/incomplete discovery page from turning an already
        mirrored future release into "extra" files eligible for prune.
        """
        known_dirs = {release.local_dir for release in self.releases}
        legacy_dirs = {legacy for release in self.releases for legacy in release.legacy_dirs}
        allowed = known_dirs.union(legacy_dirs)

        unknown: list[str] = []
        for path in self.root.iterdir():
            if not path.is_dir() or path.name in allowed or not looks_like_cycle_dir(path.name):
                continue
            if any(path.rglob("*.htm")):
                unknown.append(path.name)

        if unknown:
            raise CDCError(
                "local release directory/directories are absent from current CDC discovery: "
                + ", ".join(sorted(unknown))
                + ". Refusing to sync/prune until discovery is complete."
            )

    def legacy_candidate(self, cycle_dir: str, component_dir: str, filename: str) -> Path | None:
        """Find a safe legacy copy for directory-only migrations."""
        release = next((r for r in self.releases if r.local_dir == cycle_dir), None)
        if not release:
            return None
        for legacy_dir in release.legacy_dirs:
            candidate = self.root / legacy_dir / component_dir / filename
            if candidate.is_file() and candidate.stat().st_size >= MIN_VALID_SIZE:
                return candidate
        return None

    def download(self, url: str, path: Path, *, html: str | None = None) -> bool:
        rel = path.relative_to(self.root).as_posix()
        try:
            body = html if html is not None else self.client.fetch_text(url, allow_404=True)
            if body is None:
                raise CDCError("HTTP 404")
            atomic_write_text(path, body)
            return True
        except (CDCError, OSError) as exc:
            self.stats.failed.append(f"{rel}: {exc}")
            self.log(f"  FAIL {rel}: {exc}")
            return False

    @staticmethod
    def same_content(path: Path, remote: str) -> bool:
        try:
            local = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return normalize_html(local) == normalize_html(remote)

    def scan_expected(self) -> dict[tuple[str, str], dict[str, str]]:
        self.log("\n[1/4] Discover releases; scan and validate official CDC indexes...")
        active = self.active_component_keys()
        self.releases = self.client.discover_releases(logger=self.log)
        self.validate_discovered_release_dirs()
        expected = self.client.scan_all(
            releases=self.releases,
            include_non_public=self.include_non_public,
            active_components=active,
            logger=self.log,
        )
        self.stats.expected = sum(len(files) for files in expected.values())
        self.log(f"  Official active index total: {self.stats.expected} codebooks")
        return expected

    def synchronize(self, expected: dict[tuple[str, str], dict[str, str]]) -> None:
        self.log("\n[2/4] Reconcile local mirror...")
        for (cycle_dir, component_dir), files in sorted(expected.items()):
            for filename, url in files.items():
                path = self.expected_path(cycle_dir, component_dir, filename)
                rel = path.relative_to(self.root).as_posix()

                if not path.exists():
                    if self.check_only:
                        self.stats.missing.append(rel)
                        continue

                    legacy = self.legacy_candidate(cycle_dir, component_dir, filename)
                    if legacy is not None:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(legacy, path)
                        self.stats.migrated += 1
                        self.log(f"  MOVE-COPY {legacy.relative_to(self.root).as_posix()} -> {rel}")
                    else:
                        if not self.download(url, path):
                            self.stats.missing.append(rel)
                            continue
                        self.stats.downloaded += 1
                        self.log(f"  NEW {rel}")

                if not self.verify_content:
                    self.stats.skipped += 1
                    continue

                try:
                    remote = self.client.fetch_text(url, allow_404=True)
                except CDCError as exc:
                    self.stats.failed.append(f"{rel}: {exc}")
                    continue

                if remote is None:
                    self.stats.failed.append(f"{rel}: indexed document returned 404")
                    continue

                if self.same_content(path, remote):
                    self.stats.skipped += 1
                    continue

                if self.check_only:
                    self.stats.updated.append(rel)
                elif self.download(url, path, html=remote):
                    self.stats.updated.append(rel)
                    self.log(f"  UPDATE {rel}")

    def expected_paths(self, expected: dict[tuple[str, str], dict[str, str]]) -> set[Path]:
        result: set[Path] = set()
        for (cycle_dir, component_dir), files in expected.items():
            for filename in files:
                result.add(self.expected_path(cycle_dir, component_dir, filename))
        return result

    def detect_and_prune(self, expected: dict[tuple[str, str], dict[str, str]]) -> None:
        self.log("\n[3/4] Detect files outside the canonical official index...")
        expected_paths = self.expected_paths(expected)
        local_paths = {p.resolve() for p in self.root.rglob("*.htm") if p.is_file()}
        extras = sorted(local_paths - {p.resolve() for p in expected_paths})
        self.stats.extra = [p.relative_to(self.root).as_posix() for p in extras]

        if not extras:
            self.log("  OK no extra files")
            return

        self.log(f"  Found {len(extras)} extra/legacy files")
        for rel in self.stats.extra[:20]:
            self.log(f"    {rel}")

        if not self.prune or self.check_only:
            return

        if len(extras) > self.max_prune and not self.allow_large_prune:
            self.stats.prune_blocked = (
                f"refusing to delete {len(extras)} files; safety limit is {self.max_prune}. "
                "Review the diff and rerun with --allow-large-prune for an intentional migration."
            )
            self.log(f"  BLOCKED: {self.stats.prune_blocked}")
            return

        for path in extras:
            try:
                path.unlink()
            except OSError as exc:
                self.stats.failed.append(f"{path.relative_to(self.root).as_posix()}: delete failed: {exc}")

        self.remove_empty_directories()
        self.log(f"  Deleted {len(extras)} extra/legacy files")

    def remove_empty_directories(self) -> None:
        directories = sorted(
            (p for p in self.root.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                pass

    def refresh_missing(self, expected: dict[tuple[str, str], dict[str, str]]) -> None:
        missing: list[str] = []
        for path in sorted(self.expected_paths(expected)):
            if not path.is_file():
                missing.append(path.relative_to(self.root).as_posix())
        self.stats.missing = missing

    def scan_integrity(self) -> None:
        self.stats.empty.clear()
        self.stats.tiny.clear()
        self.stats.err404.clear()

        files = [p for p in self.root.rglob("*.htm") if p.is_file()]
        self.stats.local = len(files)
        for path in files:
            rel = path.relative_to(self.root).as_posix()
            try:
                size = path.stat().st_size
            except OSError as exc:
                self.stats.failed.append(f"{rel}: stat failed: {exc}")
                continue

            if size == 0:
                self.stats.empty.append(rel)
            elif size < MIN_VALID_SIZE:
                self.stats.tiny.append(rel)

            try:
                head = path.read_text(encoding="utf-8", errors="ignore")[:5000].lower()
            except OSError as exc:
                self.stats.failed.append(f"{rel}: read failed: {exc}")
                continue

            if (
                "page not found" in head
                or "<title>404" in head
                or "file or directory not found" in head
            ):
                self.stats.err404.append(rel)

    def report(self) -> None:
        s = self.stats
        self.log("\n" + "=" * 72)
        self.log("Integrity report")
        self.log(f"  official: {s.expected} | local: {s.local}")
        self.log(
            f"  missing: {len(s.missing)} | updated: {len(s.updated)} | "
            f"extra: {len(s.extra)}"
        )
        self.log(
            f"  empty: {len(s.empty)} | 404: {len(s.err404)} | "
            f"tiny(<{MIN_VALID_SIZE}B): {len(s.tiny)} | failures: {len(s.failed)}"
        )
        self.log(
            f"  downloaded: {s.downloaded} | migrated copies: {s.migrated} | "
            f"unchanged/skipped: {s.skipped}"
        )

        for label, items in (
            ("missing", s.missing),
            ("updated", s.updated),
            ("empty", s.empty),
            ("404", s.err404),
            ("tiny", s.tiny),
            ("failures", s.failed),
        ):
            if items:
                self.log(f"\n  {label} (first 20):")
                for item in items[:20]:
                    self.log(f"    {item}")

        if s.prune_blocked:
            self.log(f"\n  PRUNE BLOCKED: {s.prune_blocked}")

        self.log("=" * 72)

    def run(self) -> int:
        self.log("=" * 72)
        self.log(f"CDC NHANES codebook sync | root: {self.root}")
        self.log(
            f"mode: {'check-only' if self.check_only else 'sync'} | "
            f"verify-content: {self.verify_content} | prune: {self.prune}"
        )
        self.log("=" * 72)

        try:
            # Critical safety property: release discovery and every active official
            # index are validated before touching any local file.
            expected = self.scan_expected()
        except CDCError as exc:
            self.log(f"\nFATAL discovery/index validation failed: {exc}")
            self.log("No local files were changed or pruned.")
            return 1

        self.synchronize(expected)
        self.detect_and_prune(expected)
        self.refresh_missing(expected)

        self.log("\n[4/4] Local integrity scan...")
        self.scan_integrity()
        self.report()

        mismatch_in_check = self.check_only and bool(
            self.stats.missing or self.stats.updated or self.stats.extra
        )
        fatal = bool(
            self.stats.missing
            or self.stats.empty
            or self.stats.err404
            or self.stats.tiny
            or self.stats.failed
            or self.stats.prune_blocked
            or mismatch_in_check
        )
        return 1 if fatal else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync and validate CDC NHANES codebook HTML files")
    parser.add_argument("--target", default=DEFAULT_ROOT, help="mirror root directory")
    parser.add_argument("--check", action="store_true", help="read-only verification; return 1 on any mismatch")
    parser.add_argument("--prune", action="store_true", help="delete files not present in the canonical official index")
    parser.add_argument("--quiet", action="store_true", help="reduce log output")
    parser.add_argument(
        "--no-verify-content",
        action="store_true",
        help="only fill missing files; do not fetch/compare every existing codebook",
    )
    parser.add_argument(
        "--include-non-public",
        action="store_true",
        help="also mirror publicly viewable codebooks for RDC/limited-access datasets",
    )
    parser.add_argument(
        "--max-prune",
        type=int,
        default=100,
        help="maximum automatic deletions before the safety interlock blocks pruning (default: 100)",
    )
    parser.add_argument(
        "--allow-large-prune",
        action="store_true",
        help="override the prune safety limit; intended only for reviewed migrations",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    sync = CDCSync(
        args.target,
        check_only=args.check,
        prune=args.prune,
        quiet=args.quiet,
        verify_content=not args.no_verify_content,
        include_non_public=args.include_non_public,
        max_prune=max(0, args.max_prune),
        allow_large_prune=args.allow_large_prune,
    )
    try:
        code = sync.run()
    finally:
        sync.client.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
