# NetRoute

A small macOS GUI (Python + Tkinter) to choose which network interface carries your **internet** traffic and which interface handles specific **local subnets**.

- Internet NIC: moves the chosen service to the top of the macOS Service Order and updates the default route.
- Local network NIC: adds static routes (subnet → interface, optional gateway), saved in `~/.netroute_rules.json`.

## Run

```
python3 netroute.py
```

Changes ask for your admin password. Static routes are not persistent across reboots or reconnects; use "Re-apply all rules".
