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

On first visit the dashboard prompts **"Enter Websocket URL to a running Matter
Server"** and defaults to `ws://localhost:5580/ws` — that default does **not**
work from a browser (localhost = your machine). Enter:

```text
wss://matter.${SECRET_DOMAIN}/ws
```

The value is stored in the browser's localStorage, so it's a one-time step per
browser. Envoy proxies the `/ws` WebSocket upgrade to the backend over HTTP/1.1.
