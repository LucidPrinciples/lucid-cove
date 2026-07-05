// =============================================================================
// core-datetime.js — Timezone utilities for date display and form handling
// Depends on MC global (from core.js) at call time, not parse time.
// Must load before core.js tab scripts (used by 16+ files).
// =============================================================================

// =============================================================================
// Timezone utilities — used by all tabs for date display and form handling
// =============================================================================

/** Get the effective IANA timezone for the current user.
 *  Cascade: Presence timezone → Cove timezone → America/New_York */
function getTimezone() {
    return MC.presence?.timezone || MC.instance?.timezone || 'America/New_York';
}

/** Format an ISO datetime string for display in the user's timezone.
 *  opts: Intl.DateTimeFormat options (defaults to date + time). */
function formatDate(isoString, opts) {
    if (!isoString) return '';
    const d = new Date(isoString);
    if (isNaN(d)) return isoString;
    const defaults = { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' };
    return new Intl.DateTimeFormat('en-US', { timeZone: getTimezone(), ...defaults, ...(opts || {}) }).format(d);
}

/** Format just the date portion in the user's timezone. */
function formatDateOnly(isoString) {
    return formatDate(isoString, { hour: undefined, minute: undefined });
}

/** Format just the time portion in the user's timezone. */
function formatTime(isoString) {
    return formatDate(isoString, { year: undefined, month: undefined, day: undefined });
}

/** Convert a datetime-local input value to a naive ISO string for the backend.
 *  datetime-local gives "2026-05-31T17:00" — the user's intended local time.
 *  Backend resolves timezone via Presence/Cove settings (time_utils.py).
 *  No offset math on the frontend — that's the backend's job. */
function localInputToISO(dtLocalValue) {
    if (!dtLocalValue) return dtLocalValue;
    // datetime-local values are "YYYY-MM-DDTHH:MM" — add seconds if missing
    if (dtLocalValue.length === 16) return dtLocalValue + ':00';
    return dtLocalValue;
}

/** Convert an ISO datetime to a value suitable for a datetime-local input.
 *  Converts to the user's timezone for display. */
function isoToLocalInput(isoString) {
    if (!isoString) return '';
    const d = new Date(isoString);
    if (isNaN(d)) return '';
    const tz = getTimezone();
    const fmt = new Intl.DateTimeFormat('en-CA', {
        timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hour12: false,
    });
    const parts = fmt.formatToParts(d);
    const get = (type) => (parts.find(p => p.type === type) || {}).value || '00';
    return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`;
}
