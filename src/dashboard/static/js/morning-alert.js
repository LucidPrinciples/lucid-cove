// =============================================================================
// morning-alert.js — best-effort local morning open (habit wedge)
// =============================================================================
// When enabled, arm a timer for the next local_time and show a Notification
// (if permitted) that deep-links to /?tab=tune&view=latest.
// Honest limits: iOS Safari background tabs rarely fire; installed PWA is better;
// true lock-screen reliability needs a native shell later. Preference still saves.
// =============================================================================

(function () {
    let _timer = null;
    let _armedKey = '';

    function _openLatestUrl() {
        const u = new URL(location.origin + '/');
        u.searchParams.set('tab', 'tune');
        u.searchParams.set('view', 'latest');
        return u.toString();
    }

    function _msUntilLocalTime(hhmm) {
        const parts = String(hhmm || '07:00').split(':');
        const h = parseInt(parts[0], 10);
        const m = parseInt(parts[1] || '0', 10);
        if (isNaN(h) || isNaN(m)) return null;
        const now = new Date();
        const target = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m, 0, 0);
        if (target.getTime() <= now.getTime() + 15000) {
            target.setDate(target.getDate() + 1);
        }
        return target.getTime() - now.getTime();
    }

    function _fireNotification(alert) {
        const title = 'Your morning tuning';
        const body = 'Open today’s latest practice and music.';
        const url = _openLatestUrl();
        const go = () => {
            try {
                sessionStorage.setItem('mc_open_latest_tuning', '1');
            } catch (e) {}
            // Prefer same-tab navigation so cold start hits view=latest
            location.href = url;
        };

        if (typeof Notification === 'undefined' || Notification.permission !== 'granted') {
            // No permission — still try foreground soft path if app is open
            if (document.visibilityState === 'visible' && typeof switchToTab === 'function') {
                try { sessionStorage.setItem('mc_open_latest_tuning', '1'); } catch (e) {}
                switchToTab('tune');
            }
            return;
        }

        try {
            const n = new Notification(title, {
                body: body,
                tag: 'morning-open-latest',
                renotify: true,
                data: { url: url },
            });
            n.onclick = function () {
                try { window.focus(); } catch (e) {}
                go();
                try { n.close(); } catch (e) {}
            };
        } catch (e) {
            // Some browsers throw if not secure context
            console.warn('[morning-alert] notify failed', e);
        }
    }

    function _clear() {
        if (_timer) {
            clearTimeout(_timer);
            _timer = null;
        }
        _armedKey = '';
    }

    window._morningAlertReschedule = function (alert) {
        _clear();
        if (!alert || !alert.enabled) return;
        const ms = _msUntilLocalTime(alert.local_time || '07:00');
        if (ms == null) return;
        const key = (alert.local_time || '07:00') + '|' + String(!!alert.enabled);
        _armedKey = key;
        _timer = setTimeout(function () {
            if (_armedKey !== key) return;
            _fireNotification(alert);
            // Re-arm for the following day
            window._morningAlertReschedule(alert);
        }, ms);
    };

    async function _bootMorningAlert() {
        try {
            const res = await fetch('/api/tuning/morning-alert');
            if (!res.ok) return;
            const data = await res.json();
            const alert = (data && data.morning_alert) || null;
            if (!MC.features) MC.features = {};
            if (alert) MC.features.morning_alert = alert;
            window._morningAlertReschedule(alert);
        } catch (e) { /* non-fatal */ }
    }

    // After MC boot settles
    if (document.readyState === 'complete') {
        setTimeout(_bootMorningAlert, 1200);
    } else {
        window.addEventListener('load', function () { setTimeout(_bootMorningAlert, 1200); });
    }

    // Re-arm when returning to foreground (timers throttled in background)
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible' && MC && MC.features && MC.features.morning_alert) {
            window._morningAlertReschedule(MC.features.morning_alert);
        }
    });
})();
