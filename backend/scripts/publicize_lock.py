"""Replace mirrored lock artifacts with hash-verified official PyPI artifact URLs.

Use only when migrating an existing mirrored uv.lock to public PyPI. This preserves
resolved versions/dependency markers and checks every artifact against PyPI metadata.
Normal dependency maintenance should use `uv lock`.
"""

import json
import re
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def official_artifacts(package: dict[str, Any]) -> dict[str, str]:
    name, version = package["name"], package["version"]
    endpoint = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(endpoint, timeout=30) as response:
        metadata = json.load(response)
    by_hash = {artifact["digests"]["sha256"]: artifact for artifact in metadata["urls"]}
    replacements: dict[str, str] = {}
    artifacts = [*package.get("wheels", [])]
    if package.get("sdist"):
        artifacts.append(package["sdist"])
    for artifact in artifacts:
        digest = artifact["hash"].removeprefix("sha256:")
        official = by_hash.get(digest)
        if official is None:
            raise ValueError(f"Artifact hash not found on PyPI: {name} {version} {digest}")
        if official["size"] != artifact["size"]:
            raise ValueError(f"Artifact size mismatch: {name} {version}")
        url = official["url"]
        if not url.startswith("https://files.pythonhosted.org/"):
            raise ValueError(f"Unexpected official artifact host: {name} {version}")
        replacements[artifact["url"]] = url
    return replacements


def main() -> None:
    path = ROOT / "uv.lock"
    original = path.read_text(encoding="utf-8")
    lock = tomllib.loads(original)
    packages = [package for package in lock["package"] if "registry" in package["source"]]
    replacements: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        for mapping in executor.map(official_artifacts, packages):
            replacements.update(mapping)
    updated = re.sub(
        r'source = \{ registry = "[^"]+" \}',
        'source = { registry = "https://pypi.org/simple" }',
        original,
    )
    for old, new in replacements.items():
        updated = updated.replace(f'url = "{old}"', f'url = "{new}"')
    if "pkgs.visualstudio.com" in updated or "packagefeedproxy" in updated:
        raise ValueError("Mirror references remain; refusing to publish partial conversion")
    tomllib.loads(updated)
    # Only publish after *all* metadata and hashes pass verification.
    path.write_text(updated, encoding="utf-8")
    print(
        f"Verified {len(packages)} PyPI packages, "
        f"{len(replacements)} artifacts; public lock written"
    )


if __name__ == "__main__":
    main()
