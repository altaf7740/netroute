#!/usr/bin/env python3
"""NetRoute helper (runs as root via LaunchDaemon).

Watches interface state and, whenever it changes, re-applies the subnet rules and
enforces the internet failover order from the user's NetRoute config.
Usage: netroute_daemon.py /path/to/config.json
"""
import ipaddress, json, os, re, subprocess, sys, time

CONFIG = sys.argv[1]
STATUS = "/var/tmp/netroute_status.json"
DEV_RE = re.compile(r"^[a-z]+\d+$")
SVC_RE = re.compile(r"\((\d+|\*)\) (.+)\n\(Hardware Port: .*?Device: (.*?)\)")
POLL = 3


def run(*cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return r.stdout.strip()
    except Exception:
        return ""


def load():
    """Read the user-writable config; accept only strictly validated values (we run as root)."""
    try:
        cfg = json.load(open(CONFIG))
    except Exception:
        return [], []
    rules = []
    for r in cfg.get("rules", []):
        try:
            net = ipaddress.IPv4Network(r["cidr"], strict=False)
            gw = str(ipaddress.IPv4Address(r["gw"])) if r.get("gw") else ""
            if DEV_RE.match(r["dev"]):
                rules.append(dict(cidr=str(net), dev=r["dev"], gw=gw))
        except Exception:
            continue
    pri = [p for p in cfg.get("priority", []) if isinstance(p, str)]
    return rules, pri


def services():
    out = run("networksetup", "-listnetworkserviceorder")
    return [dict(name=m.group(2), dev=m.group(3), enabled=m.group(1) != "*")
            for m in SVC_RE.finditer(out)]


def link(dev):
    if not DEV_RE.match(dev or ""):
        return ("", "")
    ip = run("ipconfig", "getifaddr", dev)
    return (ip, run("ipconfig", "getoption", dev, "router") if ip else "")


def default_route():
    m = re.search(r"default\s+(\S+)\s+\S+\s+(\S+)", run("netstat", "-rn", "-f", "inet"))
    return (m.group(1), m.group(2)) if m else ("", "")


def snapshot(rules, svcs, pri):
    devs = {r["dev"] for r in rules} | {s["dev"] for s in svcs if s["name"] in pri}
    return {d: link(d) for d in sorted(devs)}


def enforce_priority(svcs, pri, snap, log):
    if not pri:
        return
    names = [s["name"] for s in svcs]
    wanted = [n for n in pri if n in names] + [n for n in names if n not in pri]
    if wanted != names:
        run("networksetup", "-ordernetworkservices", *wanted)
        log.append("service order restored")
    by_name = {s["name"]: s["dev"] for s in svcs}
    for n in pri:
        ip, gw = snap.get(by_name.get(n, ""), ("", ""))
        if ip and gw:
            if default_route() != (gw, by_name[n]):
                run("route", "-n", "change", "default", gw)
                log.append(f"internet via {n}")
            break


def apply_rules(rules, snap, log):
    for r in rules:
        if not snap.get(r["dev"], ("", ""))[0]:
            continue
        run("route", "-n", "delete", "-net", r["cidr"])
        target = [r["gw"]] if r["gw"] else ["-interface", r["dev"]]
        run("route", "-n", "add", "-net", r["cidr"], *target)
        log.append(f'{r["cidr"]} -> {r["dev"]}')


def write_status(event, snap):
    data = dict(time=time.strftime("%H:%M:%S"), event=event,
                up=[d for d, v in snap.items() if v[0]], down=[d for d, v in snap.items() if not v[0]])
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATUS)


def main():
    last, last_mtime = None, None
    while True:
        rules, pri = load()
        svcs = services()
        snap = snapshot(rules, svcs, pri)
        try:
            mtime = os.stat(CONFIG).st_mtime
        except OSError:
            mtime = None
        if (snap, mtime) != last:
            log = []
            enforce_priority(svcs, pri, snap, log)
            apply_rules(rules, snap, log)
            write_status("; ".join(log) or "no changes needed", snap)
            last = (snap, mtime)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
