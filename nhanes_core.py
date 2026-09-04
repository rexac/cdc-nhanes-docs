#!/usr/bin/env python3
"""Shared configuration and HTTP helpers for the CDC NHANES codebook mirror."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import random
import re
import time
from typing import Iterable
from urllib.parse import urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


INDEX_URL = "https://wwwn.cdc.gov/nchs/nhanes/search/datapage.aspx"
DISCOVERY_URL = f"{INDEX_URL}?Component=Demographics"
DEFAULT_ROOT = os.environ.get("NHANES_DOCS", "/HostData/Data/cdc_docs")
MIN_VALID_SIZE = 500

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass(frozen=True)
class Release:
    """One official NHANES public release and its local directory name."""

    local_dir: str
    cycle: str
    label: str
    legacy_dirs: tuple[str, ...] = ()

    @property
    def begin_year(self) -> str:
        return self.cycle.split("-", 1)[0]

    @property
    def end_year(self) -> str:
        return self.cycle.rsplit("-", 1)[-1]


@dataclass(frozen=True)
class Component:
    api_name: str
    local_dir: str
    title_keywords: tuple[str, ...]


# Historical floor: these releases are always checked even if CDC later changes
# the discovery UI. Future releases are NOT hard-coded; discover_releases() adds
# them automatically from the official CDC Release Cycle selectors/links.
BASE_RELEASES: tuple[Release, ...] = (
    Release("1999", "1999-2000", "1999-2000"),
    Release("2001", "2001-2002", "2001-2002"),
    Release("2003", "2003-2004", "2003-2004"),
    Release("2005", "2005-2006", "2005-2006"),
    Release("2007", "2007-2008", "2007-2008"),
    Release("2009", "2009-2010", "2009-2010"),
    Release("2011", "2011-2012", "2011-2012"),
    Release("2013", "2013-2014", "2013-2014"),
    Release("2015", "2015-2016", "2015-2016"),
    Release("2017", "2017-2018", "2017-2018"),
    Release("2017-2020", "2017-2020", "2017-March 2020 Pre-Pandemic"),
    Release("2021-2023", "2021-2023", "August 2021-August 2023", ("2021", "2023")),
)

# Backward-compatible import for third-party code. Sync logic uses dynamic discovery.
RELEASES = BASE_RELEASES

PUBLIC_COMPONENTS: tuple[Component, ...] = (
    Component("Demographics", "demographic", ("demographics",)),
    Component("Dietary", "dietary", ("dietary",)),
    Component("Examination", "examination", ("examination",)),
    Component("Laboratory", "laboratory", ("laboratory",)),
    Component("Questionnaire", "questionnaire", ("questionnaire",)),
)

NON_PUBLIC_COMPONENT = Component(
    "Non-Public",
    "non-public",
    ("limited access", "non-public"),
)

CYCLE_ALIASES = {
    "2021": "2021-2023",
    "2023": "2021-2023",
    "2021-2023": "2021-2023",
    "2017-2018": "2017",
    "2017": "2017",
    "2017-2020": "2017-2020",
}

COMPONENT_ALIASES = {
    "demographic": "demographic",
    "demographics": "demographic",
    "dietary": "dietary",
    "examination": "examination",
    "laboratory": "laboratory",
    "questionnaire": "questionnaire",
    "non-public": "non-public",
    "nonpublic": "non-public",
}

_CYCLE_RE = re.compile(r"(?<!\d)((?:19|20)\d{2}-(?:19|20)\d{2})(?!\d)")
_LOCAL_CYCLE_DIR_RE = re.compile(r"^(?:19|20)\d{2}(?:-(?:19|20)\d{2})?$")


class CDCError(RuntimeError):
    """Raised when CDC content cannot be safely interpreted."""


class CDCClient:
    def __init__(
        self,
        retries: int = 4,
        timeout: int = 30,
        min_delay: float = 0.10,
        max_delay: float = 0.25,
    ) -> None:
        self.retries = retries
        self.timeout = timeout
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def close(self) -> None:
        self.session.close()

    def _delay(self) -> None:
        if self.max_delay > 0:
            time.sleep(random.uniform(self.min_delay, self.max_delay))

    def fetch_text(self, url: str, *, allow_404: bool = False) -> str | None:
        last_error = "unknown error"
        for attempt in range(self.retries):
            try:
                response = self.session.get(url, timeout=self.timeout)
                if response.status_code == 200:
                    response.encoding = "utf-8"
                    self._delay()
                    return response.text
                if response.status_code == 404 and allow_404:
                    self._delay()
                    return None
                last_error = f"HTTP {response.status_code}"
            except requests.RequestException as exc:
                last_error = str(exc)

            if attempt < self.retries - 1:
                time.sleep((2**attempt) + random.uniform(0.0, 0.5))

        raise CDCError(f"request failed after {self.retries} attempts: {url} ({last_error})")

    @staticmethod
    def index_url(release: Release, component: Component) -> str:
        return f"{INDEX_URL}?{urlencode({'Component': component.api_name, 'Cycle': release.cycle})}"

    @staticmethod
    def _valid_cycle(value: str) -> bool:
        match = re.fullmatch(r"((?:19|20)\d{2})-((?:19|20)\d{2})", value)
        if not match:
            return False
        begin, end = map(int, match.groups())
        return begin >= 1999 and end >= begin and (end - begin) <= 5

    @classmethod
    def extract_release_cycles(cls, html: str) -> set[str]:
        """Extract actual published release cycles from the Demographics data table."""

        soup = BeautifulSoup(html, "html.parser")
        candidates: set[str] = set()

        # The unfiltered Demographics page is the most conservative release registry:
        # each real public release has one demographics codebook, while other component
        # pages can contain pooled/surplus rows such as 1999-2004 that are not cycles.
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            years = cells[0].get_text(" ", strip=True)
            if cls._valid_cycle(years):
                candidates.add(years)

        return candidates

    @staticmethod
    def release_from_cycle(cycle: str) -> Release:
        for release in BASE_RELEASES:
            if release.cycle == cycle:
                return release
        # New releases use the complete official cycle as their local directory.
        # This avoids guessing whether a future collection is two or three years long.
        return Release(cycle, cycle, cycle)

    def discover_releases(self, *, logger=print) -> tuple[Release, ...]:
        """Discover actual published release cycles from CDC Demographics data."""

        try:
            html = self.fetch_text(DISCOVERY_URL)
        except CDCError as exc:
            raise CDCError(f"release discovery failed: {exc}") from exc
        assert html is not None

        discovered = self.extract_release_cycles(html)
        if not discovered:
            raise CDCError("release discovery failed: no published cycle rows found")

        baseline_cycles = {release.cycle for release in BASE_RELEASES}
        missing_baseline = baseline_cycles.difference(discovered)
        if missing_baseline:
            raise CDCError(
                "release discovery looks incomplete/suspicious; known published cycles "
                "are missing from CDC Demographics table: " + ", ".join(sorted(missing_baseline))
            )

        releases = tuple(
            self.release_from_cycle(cycle)
            for cycle in sorted(
                discovered,
                key=lambda value: tuple(int(part) for part in value.split("-", 1)),
            )
        )

        dynamic = [release.cycle for release in releases if release.cycle not in baseline_cycles]
        logger(
            f"  Release discovery: {len(releases)} published cycle(s) "
            f"({len(dynamic)} dynamic)"
        )
        if dynamic:
            logger(f"  New dynamically discovered release(s): {', '.join(dynamic)}")
        return releases

    def scan_index(
        self,
        release: Release,
        component: Component,
        *,
        allow_empty: bool = False,
    ) -> dict[str, str]:
        """Return {filename: document_url} after validating the index page."""

        url = self.index_url(release, component)
        html = self.fetch_text(url)
        assert html is not None
        soup = BeautifulSoup(html, "html.parser")

        heading = " ".join(
            x
            for x in (
                soup.title.get_text(" ", strip=True) if soup.title else "",
                soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else "",
            )
            if x
        )
        heading_lower = heading.lower()

        if "comprehensive data list" in heading_lower:
            raise CDCError(
                f"CDC returned Comprehensive Data List fallback for {release.cycle}/{component.api_name}"
            )

        if not any(keyword in heading_lower for keyword in component.title_keywords):
            raise CDCError(
                f"unexpected CDC page for {release.cycle}/{component.api_name}: {heading!r}"
            )

        if release.begin_year not in heading or release.end_year not in heading:
            raise CDCError(
                f"unexpected cycle page for {release.cycle}/{component.api_name}: {heading!r}"
            )

        links: dict[str, str] = {}
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if not (href.lower().endswith(".htm") and "datafiles" in href.lower()):
                continue
            full = urljoin(url, href)
            filename = Path(urlparse(full).path).name
            if filename:
                links[filename] = full

        if not links and not allow_empty:
            raise CDCError(f"no codebook links found for {release.cycle}/{component.api_name}")

        if component.api_name == "Demographics" and len(links) > 10:
            raise CDCError(
                f"suspicious Demographics index ({len(links)} links) for {release.cycle}; refusing to continue"
            )

        return dict(sorted(links.items()))

    def scan_all(
        self,
        *,
        releases: Iterable[Release] | None = None,
        include_non_public: bool = False,
        active_components: set[tuple[str, str]] | None = None,
        logger=print,
    ) -> dict[tuple[str, str], dict[str, str]]:
        """Scan all active releases, safely tolerating unpublished future candidates.

        Historical/base releases are strict. A newly discovered future release may exist
        in CDC navigation before public-use codebooks are published. Such empty/fallback
        components are reported as WAIT and omitted. Once a component has local codebooks,
        it becomes strict too, so a later CDC glitch cannot make prune delete it.
        """

        if releases is None:
            releases = self.discover_releases(logger=logger)
        releases = tuple(releases)
        active_components = active_components or set()

        components: Iterable[Component] = PUBLIC_COMPONENTS
        if include_non_public:
            components = (*PUBLIC_COMPONENTS, NON_PUBLIC_COMPONENT)

        baseline_cycles = {release.cycle for release in BASE_RELEASES}
        expected: dict[tuple[str, str], dict[str, str]] = {}

        for release in releases:
            seen_for_release: dict[str, str] = {}
            release_total = 0
            is_baseline = release.cycle in baseline_cycles

            for component in components:
                key = (release.local_dir, component.local_dir)
                strict = is_baseline or key in active_components

                try:
                    links = self.scan_index(
                        release,
                        component,
                        allow_empty=not strict,
                    )
                except CDCError as exc:
                    if strict:
                        raise
                    logger(
                        f"  WAIT {release.local_dir:>9} {component.local_dir:<13} "
                        f"not published/usable yet ({exc})"
                    )
                    continue

                if not links:
                    logger(
                        f"  WAIT {release.local_dir:>9} {component.local_dir:<13} "
                        "0 published codebooks"
                    )
                    continue

                expected[key] = links
                release_total += len(links)
                logger(
                    f"  OK {release.local_dir:>9} {component.local_dir:<13} {len(links):>4} docs"
                )

                if component.api_name != "Non-Public":
                    overlap = set(links).intersection(seen_for_release)
                    if overlap:
                        sample = ", ".join(sorted(overlap)[:5])
                        raise CDCError(
                            f"duplicate public codebooks across components for {release.cycle}: {sample}"
                        )
                    for name in links:
                        seen_for_release[name] = component.local_dir

            if not is_baseline:
                if release_total == 0:
                    logger(f"  PENDING release {release.cycle}: no public codebooks yet")
                else:
                    logger(f"  ACTIVE dynamic release {release.cycle}: {release_total} codebooks")

        return expected


def normalize_html(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(text, encoding="utf-8", newline="")
        os.replace(temp, path)
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


def canonical_cycle(value: str) -> str:
    value = value.strip()
    return CYCLE_ALIASES.get(value, value)


def resolve_cycle_dir(root: Path, value: str) -> str:
    """Resolve a CLI cycle value without guessing the length of future cycles."""

    raw = value.strip()
    canonical = canonical_cycle(raw)
    if (root / canonical).is_dir():
        return canonical

    # For a future release, permit `--cycle 2025` to resolve to 2025-2027 (or
    # whatever CDC eventually publishes) only when there is exactly one match.
    if re.fullmatch(r"(?:19|20)\d{2}", raw):
        matches = sorted(
            path.name
            for path in root.iterdir()
            if path.is_dir() and path.name.startswith(raw + "-")
        ) if root.is_dir() else []
        if len(matches) == 1:
            return matches[0]

    return canonical


def canonical_component(value: str) -> str:
    value = value.strip().lower()
    return COMPONENT_ALIASES.get(value, value)


def looks_like_cycle_dir(value: str) -> bool:
    return bool(_LOCAL_CYCLE_DIR_RE.fullmatch(value))
