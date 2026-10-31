"""Fetch and freeze only the pinned public Carps example clouds.

This preparer does not access official hidden/sealed data. Store its output in
an evaluator-only directory, outside every agent workspace and mount.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request

import numpy as np

from .carps import UPSTREAM_COMMIT, UPSTREAM_REPOSITORY


SOURCE_MANIFEST_BLOB = "0287e439b33e0501b0c685b135ba0efe2e2cb735"
DATA_PATH = "carps-star-discrepancy-subset-select/environment/app/data/examples"


@dataclass(frozen=True)
class PublicCloud:
    case_id: str
    file: str
    n: int
    k: int
    git_blob_sha1: str
    sha256: str
    split: str
    seed: int = 0


PUBLIC_CLOUDS = (
    PublicCloud("cloud_00", "cloud_00.npy", 2436, 30, "4a8b15a9501e43afd31bf8698a5f82f9639abfd0", "e3c113b089b7b7f13429593eebcbdfb71f0374bc9b9a1b4f8e910709911d5b35", "dev"),
    PublicCloud("cloud_01", "cloud_01.npy", 3217, 65, "e82565000fff860749e340bd4787d5aa11ba08f4", "9dae1df7b994f2d75958439cafa6843725d845ab72d164b8c543d8d95c6fa356", "dev"),
    PublicCloud("cloud_02", "cloud_02.npy", 1149, 30, "af1cbf230866727313d09be7de221de1d057f4fa", "61145848214161145ef956a5ba9cbfb5700f6110d2ba38b225fe338826bc18da", "dev"),
    PublicCloud("cloud_03", "cloud_03.npy", 1374, 30, "08231ce769f3becd367dfff665367c49b1844370", "a4e9c7089ed94a809b406e81d3362701dc8dd8e6f076eae7ac68428590d0dc55", "dev"),
    PublicCloud("cloud_04", "cloud_04.npy", 3621, 50, "5172df1c7d2bedac96fea1c6adc0bacc586f5a97", "d1941a6fac230b157ed295c2a6322918bc7f36c87401a837bbc9e81cfbdd0709", "dev"),
    PublicCloud("cloud_05", "cloud_05.npy", 872, 65, "04b97aa75f2772136fee8d12ceb0bf2ee9d1209f", "611bd182922e738836f09b4050e96693fbecb30f56c243520194acca646d17c2", "validation"),
    PublicCloud("cloud_06", "cloud_06.npy", 2457, 50, "188ff123230b80d4967bab897c98d88f7480e2f7", "139339b1a17b118221fa5afede47e5c6809e2ae682d3a48d89e5e3db5353bc9b", "holdout"),
    PublicCloud("cloud_07", "cloud_07.npy", 2990, 65, "66d93a90b3666dd615bc38537dc3d96b16d2f4b8", "6b8095ee6ebe9f5e61a78b18c5d499b4ba5044dbc1955843c59599f18852594c", "holdout"),
)


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def prepare(output_root: Path, *, opener=urllib.request.urlopen) -> dict:
    """Download the eight allowlisted public files and emit a frozen manifest."""
    if output_root.exists() or output_root.is_symlink() or (
        hasattr(os.path, "isjunction") and os.path.isjunction(output_root)
    ):
        raise ValueError("Carps output directory must be a new real directory")
    output = output_root.resolve(strict=False)
    if output.exists():
        raise ValueError("Carps output directory must not already exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-prepare-", dir=output.parent))
    entries = []
    try:
        for cloud in PUBLIC_CLOUDS:
            url = (
                f"https://raw.githubusercontent.com/{UPSTREAM_REPOSITORY}/"
                f"{UPSTREAM_COMMIT}/{DATA_PATH}/{cloud.file}"
            )
            with opener(url, timeout=30) as response:
                payload = response.read(16 * 1024 * 1024 + 1)
            if len(payload) > 16 * 1024 * 1024:
                raise ValueError(f"public example exceeds 16 MiB: {cloud.case_id}")
            if _git_blob_sha1(payload) != cloud.git_blob_sha1:
                raise ValueError(f"pinned upstream blob mismatch: {cloud.case_id}")
            sha256 = hashlib.sha256(payload).hexdigest()
            if sha256 != cloud.sha256:
                raise ValueError(f"pinned upstream SHA-256 mismatch: {cloud.case_id}")
            points = np.load(io.BytesIO(payload), allow_pickle=False)
            if points.dtype != np.float64 or points.shape != (cloud.n, 3) or not points.flags.c_contiguous:
                raise ValueError(f"public cloud metadata mismatch: {cloud.case_id}")
            if not np.isfinite(points).all() or points.min() < 0 or points.max() > 1:
                raise ValueError(f"public cloud values violate [0,1]^3: {cloud.case_id}")
            if cloud.k not in {30, 50, 65} or cloud.k >= cloud.n:
                raise ValueError(f"invalid frozen subset size: {cloud.case_id}")
            split_root = staging / cloud.split
            split_root.mkdir(exist_ok=True)
            target = split_root / cloud.file
            target.write_bytes(payload)
            entries.append({
                "case_id": cloud.case_id,
                "split": cloud.split,
                "file": f"{cloud.split}/{cloud.file}",
                "n": cloud.n,
                "k": cloud.k,
                "seed": cloud.seed,
                "upstream_git_blob_sha1": cloud.git_blob_sha1,
                "sha256": sha256,
            })
        manifest = {
            "schema_version": 1,
            "upstream_repository": UPSTREAM_REPOSITORY,
            "upstream_commit": UPSTREAM_COMMIT,
            "instruction_blob_sha1": "83960625fccf0d0287a88a9a4def0828d242f136",
            "public_source_manifest_blob_sha1": SOURCE_MANIFEST_BLOB,
            "partition_policy": "lexical cloud_00..04 DEV; cloud_05 VALIDATION; cloud_06..07 public-pool HOLDOUT",
            "scoring": "mean exact 3D L-infinity star discrepancy; minimize; invalid/timeouts=1.0",
            "cases": entries,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(staging, output)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="private evaluator-only output directory")
    args = parser.parse_args()
    manifest = prepare(args.out)
    print(json.dumps({"cases": len(manifest["cases"]), "splits": {name: sum(row["split"] == name for row in manifest["cases"]) for name in ("dev", "validation", "holdout")}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
