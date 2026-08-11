"""Pinned public-source manifests and checksum-verifying downloads."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MANIFEST_PATH = Path(__file__).with_name("sources.json")


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    sha256: str
    size: int


def load_manifest(path: str | Path = MANIFEST_PATH) -> dict:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _assets(source: str, manifest: dict) -> tuple[str, Iterable[Asset]]:
    try:
        entry = manifest["sources"][source]
    except KeyError as error:
        raise ValueError(f"unknown data source: {source}") from error
    assets = tuple(Asset(**asset) for asset in entry["assets"])
    return str(entry["version"]), assets


def source_asset_paths(
    source: str,
    output_root: str | Path = "data/raw",
    *,
    manifest_path: str | Path = MANIFEST_PATH,
) -> list[Path]:
    """Return the expected local paths for a pinned source without fetching it."""
    version, assets = _assets(source, load_manifest(manifest_path))
    destination = Path(output_root) / source / version
    return [destination / asset.name for asset in assets]


def fetch_source(
    source: str,
    output_root: str | Path = "data/raw",
    *,
    force: bool = False,
    manifest_path: str | Path = MANIFEST_PATH,
) -> list[Path]:
    """Fetch every pinned source asset and verify size plus SHA256."""
    manifest = load_manifest(manifest_path)
    version, assets = _assets(source, manifest)
    destination = Path(output_root) / source / version
    destination.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []

    for asset in assets:
        target = destination / asset.name
        if target.exists() and not force:
            if target.stat().st_size == asset.size and sha256_file(target) == asset.sha256:
                results.append(target)
                continue
            raise ValueError(
                f"existing asset failed verification: {target}; use --force to replace it"
            )

        temporary = target.with_suffix(target.suffix + ".part")
        if temporary.exists():
            temporary.unlink()
        try:
            with urllib.request.urlopen(asset.url) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if temporary.stat().st_size != asset.size:
                raise ValueError(
                    f"size mismatch for {asset.name}: expected {asset.size}, "
                    f"got {temporary.stat().st_size}"
                )
            actual_checksum = sha256_file(temporary)
            if actual_checksum != asset.sha256:
                raise ValueError(
                    f"checksum mismatch for {asset.name}: expected {asset.sha256}, "
                    f"got {actual_checksum}"
                )
            os.replace(temporary, target)
        except BaseException:
            if temporary.exists():
                temporary.unlink()
            raise
        results.append(target)
    return results
