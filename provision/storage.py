"""CF-98 — storage.data_root: one knob that relocates a Cove's big named volumes
to bind mounts under a chosen drive.

Spec: Projects/OSS-Flip-Reorg/storage-architecture-spec.md (LOCKED 2026-07-02).

Design contract honored here:
- `storage.data_root` empty / absent  ==>  today's Docker named-volume behavior,
  byte-for-byte. Zero change for every existing zero-config install.
- `storage.data_root` set (e.g. /data/lucidcove)  ==>  the provisioner emits BIND
  mounts to a fixed sublayout for the LARGE, churny volumes:
      {data_root}/nextcloud-data  -> nextcloud data volume
      {data_root}/app-data        -> app /app/data
  Postgres stays on the OS drive (Decision 1) unless `db_on_data_root: true`.
  Redis + voice cache stay named (small / model-cache, not in the sublayout).
- Escape hatch: `storage.paths.<name>` overrides a single source path without
  touching the others (spec: "no per-thing path soup unless explicitly overridden").

This module ONLY decides the compose volume SOURCES. Creating the dirs with the
right uid/gid (NC www-data/33, app 1000) and the host-side drive detect are
install.sh's job (host-only; the container can never see drives) — see the spec's
onboarding hook. `content` and `models` in the spec's sublayout are host-level
(Syncthing bind + host Ollama OLLAMA_MODELS), also install.sh, not this compose.
"""

# Container mount TARGETS are kept identical to today on purpose: we relocate the
# backing bytes, we do NOT re-split what persists. The finer /var/www/html/data-only
# split in the spec changes NC persistence semantics and needs live verification, so
# it stays with the founder migration runbook, not the new-install code path.
_RELOCATABLE = {
    # logical name : (default named-volume, sublayout dirname under data_root)
    "nextcloud_data": ("nextcloud_data", "nextcloud-data"),
    "app_data": ("app_data", "app-data"),
    "postgres_data": ("postgres_data", "postgres-data"),
}


def _clean_root(data_root: str) -> str:
    """Absolute, trailing-slash-stripped. Empty stays empty (= named volumes)."""
    r = (data_root or "").strip().rstrip("/")
    return r


def storage_layout(cove: dict) -> dict:
    """Resolve the storage backing for build_compose.

    Returns:
      {
        "sources": {logical_name: "<named-vol>" | "/abs/bind/path"},
        "named_volumes": [names still declared in the top-level `volumes:` block],
      }
    A source that is an absolute path (starts with "/") is a bind mount and must
    NOT be re-declared under top-level `volumes:`; a bare name is a named volume
    and MUST be declared there.
    """
    storage = (cove.get("storage") or {})
    data_root = _clean_root(storage.get("data_root", ""))
    db_on_data_root = bool(storage.get("db_on_data_root", False))
    overrides = (storage.get("paths") or {})

    sources = {}
    for name, (named_vol, subdir) in _RELOCATABLE.items():
        if name in overrides and str(overrides[name]).strip():
            # explicit escape hatch wins outright
            sources[name] = str(overrides[name]).strip().rstrip("/")
            continue
        if not data_root:
            sources[name] = named_vol            # today's behavior
            continue
        if name == "postgres_data" and not db_on_data_root:
            sources[name] = named_vol            # Decision 1: DB stays on OS drive
            continue
        sources[name] = f"{data_root}/{subdir}"  # bind under the chosen drive

    named_volumes = [s for s in sources.values() if not s.startswith("/")]
    return {"sources": sources, "named_volumes": named_volumes}
