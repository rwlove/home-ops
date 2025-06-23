# After whole home power outage or all nodes power cycle

The main problem is that the kube-vip pods are not running so the VIP, typically 192.16.6.1, is unknown. It just needs to be set so that the kube control plane can get up and runnging and the kube-vip pods can get re-instantiated. To do this simply login to master1 and run the following command.

`ip addr add 192.168.6.1 dev eno1`