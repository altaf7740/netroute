# NetRoute

A small macOS GUI (Python + Tkinter) to choose which network interface carries your **internet** traffic and which interface handles specific **local subnets**.

- Internet NIC: moves the chosen service to the top of the macOS Service Order and updates the default route.
- Local network NIC: adds static routes (subnet → interface, optional gateway), saved in `~/.netroute_rules.json`.

## Run

```
python3 netroute.py
```

Changes ask for your admin password. Static routes are not persistent across reboots or reconnects; use "Re-apply all rules".

## Failover and auto-reapply

- **Failover order:** rank your interfaces; the first one that has an IP and gateway carries the internet. If it disconnects, the next one takes over, and it switches back when the first returns.
- **Auto-reapply helper:** "Install / update" installs `netroute_daemon.py` as a root LaunchDaemon (`com.netroute.daemon`). It checks every 3 seconds and re-adds your subnet routes and the failover order when a NIC (re)connects. Config is read from `~/.netroute_config.json` and strictly validated; the installed helper copy is root-owned. Remove it with "Uninstall".
