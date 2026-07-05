"""CF-98 — storage.data_root provisioner tests (batch8 #13).

Proves: provision config WITHOUT data_root produces today's named-volume compose
byte-for-byte on the relocatable volumes; WITH data_root produces bind mounts under
the chosen drive for the big volumes while postgres stays on the OS drive (Decision 1)
unless db_on_data_root is set. Pure string generation — no live docker required.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "provision"))

import storage  # noqa: E402
from centralized import build_compose  # noqa: E402

_COVE = {"id": "lucidcove-test", "name": "Test Cove", "_app_port": 8090}


def _compose(storage_cfg=None):
    cove = dict(_COVE)
    if storage_cfg is not None:
        cove["storage"] = storage_cfg
    return build_compose(cove, deploy={}, voice_local=False)


# ---- storage_layout unit ----------------------------------------------------

def test_layout_absent_is_all_named_volumes():
    lay = storage.storage_layout({"id": "x"})
    assert lay["sources"] == {
        "nextcloud_data": "nextcloud_data",
        "app_data": "app_data",
        "postgres_data": "postgres_data",
    }
    assert set(lay["named_volumes"]) == {"nextcloud_data", "app_data", "postgres_data"}


def test_layout_data_root_binds_big_volumes_keeps_postgres_named():
    lay = storage.storage_layout({"storage": {"data_root": "/data/lucidcove/"}})
    assert lay["sources"]["nextcloud_data"] == "/data/lucidcove/nextcloud-data"
    assert lay["sources"]["app_data"] == "/data/lucidcove/app-data"
    # Decision 1: DB stays on the OS drive unless explicitly moved.
    assert lay["sources"]["postgres_data"] == "postgres_data"
    assert lay["named_volumes"] == ["postgres_data"]


def test_layout_db_on_data_root_binds_postgres_too():
    lay = storage.storage_layout(
        {"storage": {"data_root": "/mnt/tank", "db_on_data_root": True}})
    assert lay["sources"]["postgres_data"] == "/mnt/tank/postgres-data"
    assert lay["named_volumes"] == []


def test_layout_explicit_path_override_wins():
    lay = storage.storage_layout(
        {"storage": {"data_root": "/data/x", "paths": {"nextcloud_data": "/nas/nc/"}}})
    assert lay["sources"]["nextcloud_data"] == "/nas/nc"
    assert lay["sources"]["app_data"] == "/data/x/app-data"


# ---- compose generation integration ----------------------------------------

def test_compose_without_data_root_uses_named_volumes():
    out = _compose(storage_cfg=None)
    assert "- nextcloud_data:/var/www/html" in out
    assert "- app_data:/app/data" in out
    assert "- postgres_data:/var/lib/postgresql/data" in out
    # all three still declared in the top-level volumes block
    vol_block = out.split("volumes:")[-1]
    assert "nextcloud_data:" in vol_block
    assert "app_data:" in vol_block
    assert "postgres_data:" in vol_block


def test_compose_with_data_root_binds_and_drops_named_decls():
    out = _compose(storage_cfg={"data_root": "/data/lucidcove"})
    assert "- /data/lucidcove/nextcloud-data:/var/www/html" in out
    assert "- /data/lucidcove/app-data:/app/data" in out
    # postgres unmoved
    assert "- postgres_data:/var/lib/postgresql/data" in out
    # the bound volumes must NOT be re-declared as named volumes
    vol_block = out.rsplit("volumes:", 1)[-1].split("networks:")[0]
    assert "nextcloud_data:" not in vol_block
    assert "app_data:" not in vol_block
    assert "postgres_data:" in vol_block   # still named
    assert "redis_data:" in vol_block


def test_compose_db_on_data_root_moves_postgres():
    out = _compose(storage_cfg={"data_root": "/data/lucidcove", "db_on_data_root": True})
    assert "- /data/lucidcove/postgres-data:/var/lib/postgresql/data" in out
    vol_block = out.rsplit("volumes:", 1)[-1].split("networks:")[0]
    # only redis remains named
    assert "postgres_data:" not in vol_block
    assert "redis_data:" in vol_block
