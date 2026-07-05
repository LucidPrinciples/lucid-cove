"""
Hub registration retry — audit C3-5.

Wizard finalize registers the Cove with the Haven hub once, best-effort. On a
fresh box (DNS propagating, network warming) that one shot can fail silently:
the Cove never joins the network, and the referred_by affiliate edge — SET-ONCE
on the hub — is lost permanently if a later partial re-register (e.g. the
Matrix space sync, which omits owner_handle/domain/referred_by) lands first.

Fix: finalize persists the FULL registration payload under system_settings when
the attempt fails; the scheduler retries it until the hub acks, then clears it.
"""

import json
import logging

log = logging.getLogger("hub_retry")

_KEY = "hub_registration_pending"


async def mark_registration_pending(payload: dict) -> None:
    """Persist a failed registration's full payload for the scheduler retry."""
    try:
        from src.utils.settings import update_setting
        await update_setting(_KEY, json.dumps(payload))
        log.info("hub registration queued for retry: %s", payload.get("cove_id"))
    except Exception as e:
        log.warning("could not persist pending hub registration: %s", e)


async def clear_registration_pending() -> None:
    try:
        from src.utils.settings import update_setting
        await update_setting(_KEY, "")
    except Exception:
        pass


async def retry_pending_registration() -> None:
    """Scheduler job: re-send a pending hub registration with its full payload
    (keeps the set-once referred_by edge intact). No-op when nothing is pending.
    Never raises."""
    try:
        from src.utils.settings import get_setting
        raw = (await get_setting(_KEY, default="")) or ""
        if not raw.strip():
            return
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not payload.get("cove_id"):
            await clear_registration_pending()   # unusable record — drop it
            return
        from src.dashboard.routes import registry_client
        res = await registry_client.register_cove(**payload)
        if res.get("ok"):
            await clear_registration_pending()
            log.info("pending hub registration landed: %s", payload.get("cove_id"))
        else:
            log.info("pending hub registration still failing (will retry): %s",
                     res.get("reason"))
    except Exception as e:
        log.warning("hub registration retry errored (will retry): %s", e)
