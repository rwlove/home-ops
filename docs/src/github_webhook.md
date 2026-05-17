# GitHub Webhook → Flux

Configures GitHub to ping Flux on every push so reconciliation happens immediately instead of waiting for the next polling interval.

## Setup

1. Get the webhook URL from the Flux `Receiver`:

   ```sh
   kubectl -n flux-system get receivers.notification.toolkit.fluxcd.io
   ```

   The `URL` column contains the public path (e.g. `/hook/<token>`); concatenate it with the public ingress (`https://flux-webhook.${SECRET_DOMAIN}`).

2. Get the shared secret:

   ```sh
   kubectl -n flux-system describe secret github-webhook-token
   ```

3. In GitHub → repo Settings → Webhooks → Add webhook:
   - **Payload URL**: `https://flux-webhook.${SECRET_DOMAIN}/hook/<token>`
   - **Content type**: `application/json`
   - **Secret**: paste the value from step 2
   - **SSL verification**: Enable
   - **Which events**: Just the push event
   - **Active**: checked

## Verifying

After pushing a commit, the webhook delivery should show a `200 OK` in GitHub → Webhooks → Recent Deliveries. On the cluster side:

```sh
kubectl -n flux-system logs deploy/notification-controller --tail=50 | grep -i webhook
```

Successful trigger looks like `handling event ...source.toolkit.fluxcd.io/v1`. Failed deliveries usually mean the secret doesn't match or the public URL is wrong.

## Related

- [Cluster Rebuild](cluster_rebuild.md) — among the post-rebuild tasks, the `KUBECONFIG` Actions secret also needs refreshing.
