### To Fix permission deinied errors
1. Scale Down the service in K8s (kubectl scale --replicas=0 deploy/<serviceName>)
2. Go into the Longhorn UI, click on Volume, and find the PV. It should be in a "Detached" state. Click on the "Name" field.
3. In the Volume details screen, click the --- in the upper right and select "Attach"
4. Pick any of your nodes to attach the PV to, do not select the Maintenance Mode checkbox.
5. Take note of the 'Attached Node & Endpoint' in the 'Volume Details' screen, this should be the name of the server that you told it to attach to plus the path on that server that the block device is available (/dev/longhorn/<pv-name> )
6. SSH into the server you mounted the block device on, create a temporary mountpoint for the block device to mount to (sudo mkdir /mnt/tmp
7. Mount the block device to the temporary mount point (sudo mount /dev/longhorn<pv-name> /mnt/tmp)
8. Chown the folders to use the UID/GID that the container is operating under. In the case of the kah images, that's 568:568. (sudo chown -R 568:568 /mnt/tmp/)
9. Umount the mountpoint (sudo umount /mnt/tmp)
10. Go back into the Longhorn UI and click the same --- in the upper right and select "Detach", click OK in the menu that pops up.
11. Scale back up the service in kubernetes (kubectl scale --replicas=1 deploy/<serviceName>)
