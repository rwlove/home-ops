# Node-RED

Visual, flow-based automation that complements Home Assistant for wiring
IoT devices and services together. Deployed as an app-template
`StatefulSet` in the `home` namespace.

## Deployment notes

- **Image:** `ghcr.io/node-red/node-red`; editor/UI on port `1880`,
  exposed via the internal HTTPRoute at `node-red.${SECRET_DOMAIN}`.
- **IoT VLAN:** a Multus secondary NIC (`node-red-iot-static`) attaches the
  pod directly to the IoT VLAN (20) so flows can reach IoT devices, mDNS,
  and broadcasts; the pod is pinned via node affinity to `vlan-iot` nodes.
- **Persistence:** flows and credentials live on the `node-red-data-pvc`
  Longhorn volume (projects mode enabled).
- **Runs as UID/GID 568** with `NET_ADMIN`/`NET_RAW` for IoT probing.

## Integrations

- **Home Assistant** — via the `node-red-contrib-home-assistant-websocket`
  palette. Install it through the Palette Manager, per the
  [upstream guide](https://zachowj.github.io/node-red-contrib-home-assistant-websocket/guide/#using-the-palette-manager).
- **MQTT** — the in-namespace EMQX broker.
- **External HTTP** — arbitrary HTTP-request nodes (egress allowed to
  ports 80/443); flow URLs live in the flow DB and change at user
  discretion.
