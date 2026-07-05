/**
 * Referral Tracker — drop this script on any Lucid Principles site.
 *
 * Detects ?ref=, ?a=, or ?aff_id= in the URL. If found, bounces the user
 * through app.lucidcove.org/r/{code} to set a 90-day first-party cookie
 * on the signup domain, then redirects right back to the current page
 * (minus the ref param so it doesn't loop).
 *
 * Usage: <script src="https://app.lucidcove.org/static/ref-tracker.js"></script>
 *        (or inline the contents)
 */
(function() {
  var params = new URLSearchParams(window.location.search);
  var code = params.get('ref') || params.get('a') || params.get('aff_id');
  if (!code) return;

  // Also store locally so the current site can use it
  try { localStorage.setItem('lucid_referral_code', code); } catch(e) {}

  // Build the return URL without the ref param (prevents redirect loop)
  params.delete('ref');
  params.delete('a');
  params.delete('aff_id');
  var returnUrl = window.location.origin + window.location.pathname;
  var remaining = params.toString();
  if (remaining) returnUrl += '?' + remaining;

  // Bounce through the signup domain to set the cookie there
  window.location.replace(
    'https://app.lucidcove.org/r/' + encodeURIComponent(code)
    + '?to=' + encodeURIComponent(returnUrl)
  );
})();
