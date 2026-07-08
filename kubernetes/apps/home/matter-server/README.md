# matter-server

[python-matter-server](https://github.com/home-assistant-libs/python-matter-server)
— the Matter controller Home Assistant's Matter integration talks to.

## Networking

Runs with a Multus macvlan secondary NIC (`net1`) on the IoT VLAN (20,
`matter-server-iot-static`) and `--primary-interface=net1`, so Matter device
traffic (mDNS / IPv6 multicast / commissioning) reaches the IoT segment and
bypasses Cilium. The pod's primary `eth0` stays on the cluster network for the
WebSocket API, the LoadBalancer VIP, and startup egress (PAA certs / DCL vendor
info — see the `egress` block in `app/cnp-allow.yaml`).

## Dashboard UI

The image bundles the matter-server dashboard, served on port `5580` and exposed
at `https://matter.${SECRET_DOMAIN}/` (internal HTTPRoute) and on the
LoadBalancer VIP (`${SVC_MATTER_ADDR}`).

The bundled dashboard auto-connects its WebSocket **only** when the page URL
contains `:5580` (or an HA add-on ingress path); otherwise it prompts for a WS
URL. So there are two ways in:

- **Zero-prompt (recommended):** hit the LoadBalancer VIP on 5580 —
  `http://${SVC_MATTER_ADDR}:5580/`. The `:5580` in the URL makes the dashboard
  self-connect to `ws://${SVC_MATTER_ADDR}:5580/ws`, no prompt. This is what the
  Glance tile links to (`glance/url` on the HTTPRoute in `helmrelease.yaml`).
- **Pretty HTTPS host:** `https://matter.${SECRET_DOMAIN}/` (internal HTTPRoute).
  Because the URL has no `:5580`, the dashboard prompts once — enter
  `wss://matter.${SECRET_DOMAIN}/ws` (envoy proxies the `/ws` upgrade over
  HTTP/1.1). The value is stored in that browser's localStorage, so it's a
  one-time step per browser.
