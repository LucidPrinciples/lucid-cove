# Bundled Caddy for a SELF-HOST Cove stack.
#
# Built with the acme-dns DNS provider so a self-hoster on a private/mesh-only box
# gets a real Let's Encrypt cert for {domain} + *.{domain} via DNS-01 — using a
# scoped acme-dns credential, NEVER our Cloudflare token. (The founder host Caddy
# uses caddy-dns/cloudflare; self-host boxes use caddy-dns/acmedns instead.)
#
# The generated Cove compose mounts the rendered Caddyfile (provision builds it via
# netconfig.build_selfhost_caddyfile) and this image serves :80/:443 inside the
# Cove's own network, routing to the app/dendrite/nextcloud service names.
FROM caddy:2-builder@sha256:26f5271c26850df54ef7a9f3704b5d688c1ad51b5cde509c9d011b4e95cc8397 AS builder
# acmedns = lucidcove.org subdomains (scoped, no token shipped).
# cloudflare = a self-hoster's OWN domain behind NAT (DNS-01 with their own token).
RUN xcaddy build \
    --with github.com/caddy-dns/acmedns \
    --with github.com/caddy-dns/cloudflare

FROM caddy:2@sha256:af5fdcd76f2db5e4e974ee92f96ee8c0fc3edb55bd4ba5032547cbf3f65e486d
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
# Caddyfile is bind-mounted by the Cove compose at /etc/caddy/Caddyfile.
