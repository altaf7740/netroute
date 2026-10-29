#!/usr/bin/env python3
"""NetRoute: pick one NIC for internet and other NICs for specific local subnets (macOS)."""
import json, os, re, subprocess, tempfile, tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

CONFIG_FILE = Path.home() / ".netroute_config.json"
LEGACY_FILE = Path.home() / ".netroute_rules.json"
DAEMON_SRC = Path(__file__).resolve().with_name("netroute_daemon.py")
DAEMON_DST = "/usr/local/libexec/netroute_daemon.py"
PLIST = "/Library/LaunchDaemons/com.netroute.daemon.plist"
STATUS_FILE = Path("/var/tmp/netroute_status.json")
CIDR_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def admin(script):
    """Run a shell script as root via the macOS admin prompt."""
    esc = script.replace("\\", "\\\\").replace('"', '\\"')
    r = subprocess.run(["osascript", "-e", f'do shell script "{esc}" with administrator privileges'],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "cancelled")


def services():
    """Ordered list of dicts: name, dev, enabled, ip, gw."""
    out, res = run("networksetup", "-listnetworkserviceorder"), []
    for m in re.finditer(r"\((\d+|\*)\) (.+)\n\(Hardware Port: .*?Device: (.*?)\)", out):
        dev = m.group(3)
        ip = run("ipconfig", "getifaddr", dev) if dev else ""
        gw = run("ipconfig", "getoption", dev, "router") if ip else ""
        res.append(dict(name=m.group(2), dev=dev, enabled=m.group(1) != "*", ip=ip, gw=gw))
    return res


def current_default():
    m = re.search(r"default\s+(\S+)\s+\S+\s+(\S+)", run("netstat", "-rn", "-f", "inet"))
    return (m.group(1), m.group(2)) if m else ("", "")


def sh(s):
    return "'" + s.replace("'", "'\\''") + "'"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NetRoute")
        self.geometry("820x780")
        self.rules, self.priority = [], []
        if CONFIG_FILE.exists():
            cfg = json.loads(CONFIG_FILE.read_text())
            self.rules, self.priority = cfg.get("rules", []), cfg.get("priority", [])
        elif LEGACY_FILE.exists():
            self.rules = json.loads(LEGACY_FILE.read_text())
        self.svcs = []

        top = ttk.LabelFrame(self, text="Active network interfaces")
        top.pack(fill="x", padx=10, pady=8)
        cols = ("name", "dev", "ip", "gw")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", height=7)
        for c, w in zip(cols, (260, 80, 140, 140)):
            self.tree.heading(c, text={"name": "Service", "dev": "Device", "ip": "IP", "gw": "Gateway"}[c])
            self.tree.column(c, width=w)
        self.tree.pack(fill="x", padx=6, pady=6)
        self.default_lbl = ttk.Label(top, text="")
        self.default_lbl.pack(anchor="w", padx=6)
        bar = ttk.Frame(top); bar.pack(fill="x", padx=6, pady=6)
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="Use selected for INTERNET", command=self.set_internet).pack(side="left", padx=8)

        fo = ttk.LabelFrame(self, text="Internet failover order (first available is used)")
        fo.pack(fill="x", padx=10, pady=4)
        self.plist = tk.Listbox(fo, height=4)
        self.plist.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        pb = ttk.Frame(fo); pb.pack(side="left", padx=6, pady=6)
        for txt, cmd in (("Add selected", self.pri_add), ("Remove", self.pri_remove),
                         ("Move up", lambda: self.pri_move(-1)), ("Move down", lambda: self.pri_move(1)),
                         ("Save & apply", self.pri_apply)):
            ttk.Button(pb, text=txt, command=cmd).pack(fill="x", pady=1)

        mid = ttk.LabelFrame(self, text="Local network rules (subnet → interface)")
        mid.pack(fill="both", expand=True, padx=10, pady=4)
        form = ttk.Frame(mid); form.pack(fill="x", padx=6, pady=6)
        self.cidr = tk.StringVar(value="192.168.1.0/24")
        self.gw = tk.StringVar()
        self.dev = tk.StringVar()
        ttk.Label(form, text="Subnet").pack(side="left")
        ttk.Entry(form, textvariable=self.cidr, width=18).pack(side="left", padx=4)
        ttk.Label(form, text="via NIC").pack(side="left")
        self.dev_cb = ttk.Combobox(form, textvariable=self.dev, width=26, state="readonly")
        self.dev_cb.pack(side="left", padx=4)
        ttk.Label(form, text="Gateway (optional)").pack(side="left")
        ttk.Entry(form, textvariable=self.gw, width=15).pack(side="left", padx=4)
        ttk.Button(form, text="Add & apply", command=self.add_rule).pack(side="left", padx=4)

        self.rlist = tk.Listbox(mid, height=6)
        self.rlist.pack(fill="both", expand=True, padx=6)
        bar2 = ttk.Frame(mid); bar2.pack(fill="x", padx=6, pady=6)
        ttk.Button(bar2, text="Remove selected", command=self.remove_rule).pack(side="left")
        ttk.Button(bar2, text="Re-apply all rules", command=self.apply_all).pack(side="left", padx=8)

        hp = ttk.LabelFrame(self, text="Auto-reapply helper (re-adds routes & failover when a NIC reconnects)")
        hp.pack(fill="x", padx=10, pady=4)
        self.helper_lbl = ttk.Label(hp, text="")
        self.helper_lbl.pack(side="left", padx=6, pady=6)
        ttk.Button(hp, text="Uninstall", command=self.helper_uninstall).pack(side="right", padx=6)
        ttk.Button(hp, text="Install / update", command=self.helper_install).pack(side="right")

        self.status = ttk.Label(self, text="Ready", anchor="w")
        self.status.pack(fill="x", padx=10, pady=4)
        self.refresh()
        self.poll_helper()

    def save(self):
        CONFIG_FILE.write_text(json.dumps(dict(rules=self.rules, priority=self.priority), indent=2))

    def poll_helper(self):
        if not os.path.exists(PLIST):
            txt = "Not installed - routes must be re-applied manually"
        else:
            try:
                st = json.loads(STATUS_FILE.read_text())
                txt = f"Running - last action {st['time']}: {st['event']}  (down: {', '.join(st['down']) or 'none'})"
            except Exception:
                txt = "Installed - waiting for first status"
        self.helper_lbl.config(text=txt)
        self.after(3000, self.poll_helper)

    def helper_install(self):
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.netroute.daemon</string>
<key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>{DAEMON_DST}</string><string>{CONFIG_FILE}</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
"""
        self.save()
        with tempfile.NamedTemporaryFile("w", suffix=".plist", delete=False) as f:
            f.write(plist)
        try:
            admin("; ".join([
                "mkdir -p /usr/local/libexec",
                f"cp {sh(str(DAEMON_SRC))} {DAEMON_DST}",
                f"chown root:wheel {DAEMON_DST}", f"chmod 755 {DAEMON_DST}",
                f"cp {sh(f.name)} {PLIST}", f"chown root:wheel {PLIST}", f"chmod 644 {PLIST}",
                "launchctl bootout system/com.netroute.daemon 2>/dev/null || true",
                f"launchctl bootstrap system {PLIST}"]))
            self.say("Helper installed and running")
        except RuntimeError as e:
            self.say(f"Failed: {e}")
        os.unlink(f.name)

    def helper_uninstall(self):
        try:
            admin("; ".join(["launchctl bootout system/com.netroute.daemon 2>/dev/null || true",
                             f"rm -f {PLIST} {DAEMON_DST}"]))
            self.say("Helper removed")
        except RuntimeError as e:
            self.say(f"Failed: {e}")

    def pri_add(self):
        sel = self.tree.selection()
        if sel:
            n = self.svcs[int(sel[0])]["name"]
            if n not in self.priority:
                self.priority.append(n)
                self.refresh()

    def pri_remove(self):
        sel = self.plist.curselection()
        if sel:
            self.priority.pop(sel[0])
            self.refresh()

    def pri_move(self, d):
        sel = self.plist.curselection()
        if sel and 0 <= sel[0] + d < len(self.priority):
            i = sel[0]
            self.priority[i], self.priority[i + d] = self.priority[i + d], self.priority[i]
            self.refresh()
            self.plist.selection_set(i + d)

    def pri_apply(self):
        """Save the order and make it the macOS Service Order (priority first)."""
        self.save()
        names = [x["name"] for x in services()]
        order = [n for n in self.priority if n in names] + [n for n in names if n not in self.priority]
        try:
            admin("networksetup -ordernetworkservices " + " ".join(sh(n) for n in order))
            self.say("Failover order saved and applied")
        except RuntimeError as e:
            self.say(f"Failed: {e}")
        self.refresh()

    def say(self, msg):
        self.status.config(text=msg)

    def refresh(self):
        self.svcs = [s for s in services() if s["enabled"] and s["ip"]]
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.svcs):
            self.tree.insert("", "end", iid=str(i), values=(s["name"], s["dev"], s["ip"], s["gw"]))
        self.dev_cb["values"] = [f'{s["name"]} ({s["dev"]})' for s in self.svcs]
        if self.svcs and not self.dev.get():
            self.dev_cb.current(0)
        gw, dev = current_default()
        self.default_lbl.config(text=f"Current internet route: via {gw or '?'} on {dev or '?'}")
        self.plist.delete(0, "end")
        up = {x["name"] for x in self.svcs}
        for i, n in enumerate(self.priority, 1):
            self.plist.insert("end", f"{i}. {n}" + ("" if n in up else "   (disconnected)"))
        self.rlist.delete(0, "end")
        for r in self.rules:
            self.rlist.insert("end", f'{r["cidr"]}  →  {r["dev"]}' + (f'  gw {r["gw"]}' if r["gw"] else ""))

    def set_internet(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showinfo("NetRoute", "Select an interface first.")
        s = self.svcs[int(sel[0])]
        if not s["gw"]:
            return messagebox.showerror("NetRoute", "That interface has no gateway (no internet).")
        self.priority = [s["name"]] + [n for n in self.priority if n != s["name"]]
        self.save()
        order = [x["name"] for x in services()]
        order = [s["name"]] + [n for n in order if n != s["name"]]
        script = ("networksetup -ordernetworkservices " + " ".join(sh(n) for n in order) +
                  f"; route -n change default {s['gw']} || route -n add default {s['gw']}")
        try:
            admin(script)
            self.say(f"Internet now via {s['name']} ({s['dev']})")
        except RuntimeError as e:
            self.say(f"Failed: {e}")
        self.refresh()

    def rule_cmd(self, r, op):
        target = f"{r['gw']}" if r["gw"] else f"-interface {r['dev']}"
        return f"route -n delete -net {r['cidr']} >/dev/null 2>&1" if op == "del" else \
               f"route -n delete -net {r['cidr']} >/dev/null 2>&1; route -n add -net {r['cidr']} {target}"

    def add_rule(self):
        cidr, gw = self.cidr.get().strip(), self.gw.get().strip()
        m = re.search(r"\((.+)\)$", self.dev.get())
        if not CIDR_RE.match(cidr) or (gw and not IP_RE.match(gw)) or not m:
            return messagebox.showerror("NetRoute", "Enter a valid subnet (e.g. 10.0.0.0/24), NIC and optional gateway.")
        r = dict(cidr=cidr, dev=m.group(1), gw=gw)
        try:
            admin(self.rule_cmd(r, "add"))
        except RuntimeError as e:
            return self.say(f"Failed: {e}")
        self.rules = [x for x in self.rules if x["cidr"] != cidr] + [r]
        self.save()
        self.say(f"{cidr} → {r['dev']} applied")
        self.refresh()

    def remove_rule(self):
        sel = self.rlist.curselection()
        if not sel:
            return
        r = self.rules.pop(sel[0])
        try:
            admin(self.rule_cmd(r, "del"))
        except RuntimeError as e:
            self.say(f"Failed: {e}")
        self.save()
        self.refresh()

    def apply_all(self):
        if not self.rules:
            return
        try:
            admin("; ".join(self.rule_cmd(r, "add") for r in self.rules))
            self.say("All rules re-applied (install the helper to do this automatically)")
        except RuntimeError as e:
            self.say(f"Failed: {e}")


if __name__ == "__main__":
    App().mainloop()
