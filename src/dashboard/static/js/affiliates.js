/**
 * Affiliates — one Lucid Tuner referral link (Tuner door).
 */
async function loadAffiliates() {
    var container = document.getElementById('affiliates-content');
    if (!container) return;

    container.innerHTML = '<div class="loading">Loading affiliate data...</div>';

    try {
        var res = await fetch('/api/account/affiliates', { credentials: 'same-origin' });
        var data = await res.json().catch(function () { return {}; });

        if (data && data.signed_in === false) {
            container.innerHTML = '<div class="empty-state">Sign in to view affiliate activity.</div>';
            return;
        }
        if (!data || !data.referral_code) {
            var msg = (data && data.error)
                ? 'Unable to load affiliate data.'
                : 'Your referral code is being set up — refresh in a moment.';
            container.innerHTML = '<div class="empty-state">' + msg + '</div>';
            return;
        }

        var code = ESC(data.referral_code);
        var url = 'https://app.lucidcove.org/r/' + code + '?to=https://app.lucidtuner.com';

        container.innerHTML = '<div class="aff-container">'
            + '<div class="aff-code-section">'
            + '<div class="aff-code-label">Your Lucid Tuner referral</div>'
            + '<div class="aff-code">' + code + '</div>'
            + '<div class="aff-commission-note">Share this link. People who sign up through it are tied to your account.</div>'
            + '</div>'
            + '<div class="aff-links-section">'
            + '<div class="aff-links-header">Referral link</div>'
            + '<div class="aff-link-row">'
            + '<span class="aff-link-label">Lucid Tuner</span>'
            + '<span class="aff-link-url">' + ESC(url) + '</span>'
            + '<button class="aff-copy-btn" type="button">Copy</button>'
            + '</div>'
            + '</div>'
            + '</div>';

        var btn = container.querySelector('.aff-copy-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                navigator.clipboard.writeText(url).then(function () {
                    btn.textContent = 'Copied!';
                    setTimeout(function () { btn.textContent = 'Copy'; }, 1500);
                });
            });
        }
    } catch (err) {
        console.error('[affiliates] Load error:', err);
        container.innerHTML = '<div class="empty-state">Unable to load affiliate data.</div>';
    }
}
