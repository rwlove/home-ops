# Coral Edge TPU

Add some instructions on:
* How to use the copr repository on CentOS Stream 9
* How to do the VM passthrough of the device (i.e. udev rules)
* NFD rules

USB Resets

`
[ +12.269474] usb 2-5: reset SuperSpeed USB device number 22 using xhci_hcd
[  +0.012155] usb 2-5: LPM exit latency is zeroed, disabling LPM.
`

Need to try this hack for the USB Resets
* https://github.com/blakeblackshear/frigate/issues/2607#issuecomment-2092965042