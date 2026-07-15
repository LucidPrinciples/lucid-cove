/**
 * Affiliates — Reports sub-tab showing referral activity and earnings.
 */

async function loadAffiliates() {
    var container = document.getElementById('affiliates-content');
    if (!container) return;

    container.innerHTML = '<div class="loading">Loading affiliate data...</div>';

    try {
        var res = await fetch('/api/account/affiliates', { credentials: 'same-origin' });
        var data = await res.json().catch(function () { return {}; });

        // Woods / Jules 1310: signed-in members with no referral_code used to look
        // "signed out". Distinguish auth vs empty program vs load failure.
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

        var stats = data.stats || {};
        var referrals = data.referrals || [];
        var code = ESC(data.referral_code);

        // Build share links — bounce through app.lucidcove.org/r/ to set cookie on signup domain
        var bounceBase = 'https://app.lucidcove.org/r/' + code;
        var shareLinks = [
            { label: 'Lucid Tuner', url: bounceBase + '?to=https://lucidtuner.com' },
            { label: 'Lucid Cove', url: bounceBase + '?to=https://lucidcove.org' },
            { label: 'Lucid Principles', url: bounceBase + '?to=https://lucidprinciples.com' },
            { label: 'Direct Signup', url: bounceBase },
        ];

        var linksHtml = shareLinks.map(function(link) {
            return '<div class="aff-link-row">'
                + '<span class="aff-link-label">' + ESC(link.label) + '</span>'
                + '<span class="aff-link-url">' + ESC(link.url) + '</span>'
                + '<button class="aff-copy-btn" onclick="navigator.clipboard.writeText(\'' + ESC(link.url) + '\');this.textContent=\'Copied!\';setTimeout(()=>{this.textContent=\'Copy\'},1500)">Copy</button>'
                + '</div>';
        }).join('');

        // Stats cards
        var statsHtml = '<div class="aff-stats">'
            + '<div class="aff-stat-card"><div class="aff-stat-num">' + (stats.total_signups || 0) + '</div><div class="aff-stat-label">Signups</div></div>'
            + '<div class="aff-stat-card"><div class="aff-stat-num">' + (stats.upgraded || 0) + '</div><div class="aff-stat-label">Upgraded</div></div>'
            + '<div class="aff-stat-card"><div class="aff-stat-num">' + (stats.l2_referrals || 0) + '</div><div class="aff-stat-label">L2 Referrals</div></div>'
            + '</div>';

        // Referral list
        var listHtml = '';
        if (referrals.length === 0) {
            listHtml = '<div class="aff-empty">No referrals yet. Share your links to start earning 30% on every upgrade.</div>';
        } else {
            listHtml = '<div class="aff-list-header">Your Referrals</div>';
            listHtml += referrals.map(function(r) {
                var tierClass = r.is_paid ? 'aff-tier-paid' : 'aff-tier-free';
                var tierLabel = (r.tier || 'free').charAt(0).toUpperCase() + (r.tier || 'free').slice(1);
                var joined = r.joined ? formatDateOnly(r.joined) : '';
                return '<div class="aff-referral-row">'
                    + '<div class="aff-referral-info">'
                    + '<span class="aff-referral-name">' + ESC(r.display_name || r.username) + '</span>'
                    + '<span class="aff-referral-date">' + joined + '</span>'
                    + '</div>'
                    + '<span class="aff-tier-badge ' + tierClass + '">' + tierLabel + '</span>'
                    + '</div>';
            }).join('');
        }

        // Earnings (LPC) — the locked model: affiliate residual is paid in LPC out
        // of LP's platform fee on MARKETPLACE activity from people you refer
        // (L1 30% / L2 10% of the net fee). Subscriptions are non-commissionable.
        // Live numbers wire up once the credit economy is deployed — stub for now.
        var earningsHtml = '<div class="aff-section">'
            + '<div class="aff-links-header">Earnings (LPC)</div>'
            + '<div class="aff-stats">'
            + '<div class="aff-stat-card"><div class="aff-stat-num">—</div><div class="aff-stat-label">Pending</div></div>'
            + '<div class="aff-stat-card"><div class="aff-stat-num">—</div><div class="aff-stat-label">Paid</div></div>'
            + '</div>'
            + '<div class="aff-empty">Residuals land here as your referrals buy and sell in the Haven. Payout (cashout) opens after the legal review.</div>'
            + '</div>';

        // Program tools — the buildout area (stubs for now).
        var programHtml = '<div class="aff-section">'
            + '<div class="aff-links-header">Program tools</div>'
            + '<div class="aff-empty">Coming soon: marketing assets, per-link tracking, payout settings, and a public affiliate signup page.</div>'
            + '</div>';

        container.innerHTML = '<div class="aff-container">'
            + '<div class="aff-code-section">'
            + '<div class="aff-code-label">Your Referral Code</div>'
            + '<div class="aff-code">' + code + '</div>'
            + '<div class="aff-commission-note">Earn a residual in LPC on marketplace activity from people you refer: 30% level-one, 10% level-two of the platform fee.</div>'
            + '</div>'
            + statsHtml
            + '<div class="aff-links-section">'
            + '<div class="aff-links-header">Share Links</div>'
            + linksHtml
            + '</div>'
            + earningsHtml
            + listHtml
            + programHtml
            + '</div>';

    } catch (err) {
        console.error('[affiliates] Load error:', err);
        container.innerHTML = '<div class="empty-state">Unable to load affiliate data.</div>';
    }
}
