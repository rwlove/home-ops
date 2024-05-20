# Coral Edge TPU

# Coral USB

## Info

I didn't seem to have to do any udev rules or build/load the apex driver when passing this device through to
frigate. I had to do those things for the Mini PCIe Coral, but the way I'm doing it now (look at frigate mount
point), it doesn't seem necessary.

## USB Resets

Whenever the coral device was attached to the Frigate container it would trigger the following entry in dmesg
and Node Feature Discovery could no longer identify that the node had the device. This resulted in the frigate
staying in a 'Pending' state until I unplugged and then plugged in the Coral USB again. It was very annoying
that if the frigate container ever terminated, I'd have to unplug and then re-plug the USB.

`
[ +12.269474] usb 2-5: reset SuperSpeed USB device number 22 using xhci_hcd
[  +0.012155] usb 2-5: LPM exit latency is zeroed, disabling LPM.
`

This hack resolved the USB reset issue: https://github.com/blakeblackshear/frigate/issues/2607#issuecomment-2092965042

# Coral Mini PCIe

## Info

Not currently working. For some reason it doesn't show up in 'lspci' in my Dell R730XD. I wonder if using a more
powerful power supply would make a difference.