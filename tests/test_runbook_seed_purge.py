# RB16 (Clearfield+Founders deploy) must not ship as a universal seed.
# Fresh installs were showing host-specific ssh commands for lp-homebase.
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import src.dashboard.routes.runbooks as rb  # noqa: E402


def test_rb16_not_in_repo_seed_dir():
    seed = _ROOT / "runbooks" / "16-deploy-main-clearfield-founders.json"
    assert not seed.exists(), "RB16 must not live in cove-core/runbooks seed"


def test_ensure_dir_purges_removed_seed_orphan(tmp_path, monkeypatch):
    seed_dir = tmp_path / "seed"
    data_dir = tmp_path / "data"
    seed_dir.mkdir()
    data_dir.mkdir()
    # Legitimate seed
    (seed_dir / "01-update-cove.json").write_text(json.dumps({"id": "update-cove", "num": 1}))
    # Orphan from an older image that still had RB16
    orphan = data_dir / "16-deploy-main-clearfield-founders.json"
    orphan.write_text(json.dumps({"id": "rb16-deploy-main", "num": 16}))

    monkeypatch.setattr(rb, "SEED_DIR", seed_dir)
    monkeypatch.setattr(rb, "RUNBOOKS_DIR", data_dir)

    rb._ensure_dir()

    assert (data_dir / "01-update-cove.json").exists()
    assert not orphan.exists(), "orphan RB16 seed must be purged on ensure"


def test_ensure_dir_does_not_copy_removed_seed_from_seed_dir(tmp_path, monkeypatch):
    seed_dir = tmp_path / "seed"
    data_dir = tmp_path / "data"
    seed_dir.mkdir()
    data_dir.mkdir()
    (seed_dir / "01-update-cove.json").write_text(json.dumps({"id": "update-cove"}))
    # Even if an old image still has RB16 in SEED_DIR, skip it
    (seed_dir / "16-deploy-main-clearfield-founders.json").write_text(
        json.dumps({"id": "rb16-deploy-main"})
    )

    monkeypatch.setattr(rb, "SEED_DIR", seed_dir)
    monkeypatch.setattr(rb, "RUNBOOKS_DIR", data_dir)

    rb._ensure_dir()

    assert (data_dir / "01-update-cove.json").exists()
    assert not (data_dir / "16-deploy-main-clearfield-founders.json").exists()
