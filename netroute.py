#!/usr/bin/env python3
"""NetRoute: pick one NIC for internet and other NICs for specific local subnets (macOS)."""
import json, re, subprocess, tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

RULES_FILE = Path.home() / ".netroute_rules.json"
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
        self.geometry("820x560")
        self.rules = json.loads(RULES_FILE.read_text()) if RULES_FILE.exists() else []
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

        self.status = ttk.Label(self, text="Ready", anchor="w")
        self.status.pack(fill="x", padx=10, pady=4)
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
        RULES_FILE.write_text(json.dumps(self.rules, indent=2))
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
        RULES_FILE.write_text(json.dumps(self.rules, indent=2))
        self.refresh()

    def apply_all(self):
        if not self.rules:
            return
        try:
            admin("; ".join(self.rule_cmd(r, "add") for r in self.rules))
            self.say("All rules re-applied (routes reset on reboot/network change, so re-apply when needed)")
        except RuntimeError as e:
            self.say(f"Failed: {e}")


if __name__ == "__main__":
    App().mainloop()
