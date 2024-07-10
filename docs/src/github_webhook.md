## Github Webhook
`kubectl -n flux-system get receivers.notification.toolkit.fluxcd.io` generates token URL to be put into
github.com -> Settings -> Webhooks -> Payload URL

* Content Type: application/json
* Secret: <token from kubectl -n flux-system describe secrets github-webhook-token>
* SSL: Enable SSL verification
* Which events would you like to trigger this webhook?: Just the push event.
* Active: <checked>
