# Coral Edge TPU

Frigate offloads object detection to a Coral USB Edge TPU attached to `worker4`. The Coral lets Frigate run real-time inference on multiple camera streams at sub-watt power without leaning on the P40 (which is reserved for heavier ML — see [`p40.md`](p40.md)).

## Coral USB (active)

The Coral USB on `worker4` is the live unit. Node selection is label-driven:

```sh
kubectl get nodes -L google.feature.node.kubernetes.io/coral-usb
```

`worker4` carries `google.feature.node.kubernetes.io/coral-usb=true`; Frigate's `nodeSelector` pins it there.

### What I didn't need to do

Unlike the Mini PCIe variant, I didn't need to install udev rules or build/load the apex driver to pass the USB Coral through to Frigate. The current mount strategy in `kubernetes/apps/home/frigate/app/helmrelease.yaml` handles everything via the standard `securityContext` + device mount path.

### USB-reset bug + the hack

Anytime the Frigate container stopped, the Coral USB went through this in `dmesg`:

```text
usb 2-5: reset SuperSpeed USB device number 22 using xhci_hcd
usb 2-5: LPM exit latency is zeroed, disabling LPM.
```

After the reset, Node Feature Discovery could no longer see the device. The Coral USB label disappeared from the node, Frigate stayed `Pending`, and the only manual fix was unplugging and re-plugging the device. Bad anytime Frigate cycled.

The workaround came from the Frigate community: <https://github.com/blakeblackshear/frigate/issues/2607#issuecomment-2092965042>. The fix bypasses the USB reset by holding the device handle open through restarts. Confirm it's still in the Frigate manifests before assuming the Coral will survive a pod restart.

## Coral Mini PCIe (not in use)

The Mini PCIe Coral does not enumerate in `lspci` on the Dell R730xd. Suspected causes:

- PCIe lane allocation conflict with another slotted card
- M.2-to-PCIe adapter incompatibility
- Insufficient power on the slot

Not pursued further since the USB variant on a worker node is the simpler topology. Revisit if the cluster ever wants per-camera dedicated TPUs.

## Related

- [`p40.md`](p40.md) — NVIDIA Tesla P40 (the heavier GPU path; not used for Frigate detection)
