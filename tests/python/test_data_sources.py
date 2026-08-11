import hashlib
import json

import pytest
from llmschedbench.data.sources import fetch_source, sha256_file, source_asset_paths


def test_fetch_source_verifies_and_reuses_asset(tmp_path):
    payload = b"fixture data\n"
    source = tmp_path / "source.jsonl"
    source.write_bytes(payload)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": {
                    "fixture": {
                        "version": "v1",
                        "assets": [
                            {
                                "name": "trace.jsonl",
                                "url": source.as_uri(),
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "size": len(payload),
                            }
                        ],
                    }
                }
            }
        )
    )
    output = tmp_path / "raw"

    first = fetch_source("fixture", output, manifest_path=manifest)
    second = fetch_source("fixture", output, manifest_path=manifest)

    assert first == second == [output / "fixture" / "v1" / "trace.jsonl"]
    assert sha256_file(first[0]) == hashlib.sha256(payload).hexdigest()
    assert source_asset_paths("fixture", output, manifest_path=manifest) == first


def test_fetch_source_rejects_an_existing_corrupt_asset(tmp_path):
    payload = b"expected"
    source = tmp_path / "source.bin"
    source.write_bytes(payload)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": {
                    "fixture": {
                        "version": "v1",
                        "assets": [
                            {
                                "name": "trace.bin",
                                "url": source.as_uri(),
                                "sha256": hashlib.sha256(payload).hexdigest(),
                                "size": len(payload),
                            }
                        ],
                    }
                }
            }
        )
    )
    target = tmp_path / "raw" / "fixture" / "v1" / "trace.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt!")

    with pytest.raises(ValueError, match="failed verification"):
        fetch_source("fixture", tmp_path / "raw", manifest_path=manifest)
