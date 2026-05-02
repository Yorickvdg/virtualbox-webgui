import subprocess
import re
import sys
import threading
import time
from collections import deque, defaultdict
from flask import Flask, render_template, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ── Config ─────────────────────────────────────────────────────────────────────
VBOXMANAGE_PATHS = [
    r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe",
    r"C:\Program Files\Oracle\VirtualBox 7.1\VBoxManage.exe",
    "VBoxManage",  # if on PATH
]
METRICS_INTERVAL = 3       # seconds between metric polls
HISTORY_SIZE     = 60      # data points kept per metric (~3 min at 3s)

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# ── State ──────────────────────────────────────────────────────────────────────
metrics_cache   = {}       # {vm_name: {cpu_total, ram_percent, ...}}
metrics_history = defaultdict(lambda: {
    "cpu":        deque(maxlen=HISTORY_SIZE),
    "ram_percent": deque(maxlen=HISTORY_SIZE),
    "net_rx":     deque(maxlen=HISTORY_SIZE),
    "net_tx":     deque(maxlen=HISTORY_SIZE),
    "timestamps": deque(maxlen=HISTORY_SIZE),
})
cache_lock = threading.Lock()
vboxmanage_path = None


# ── Helpers ────────────────────────────────────────────────────────────────────
def find_vboxmanage():
    global vboxmanage_path
    import shutil, os
    for p in VBOXMANAGE_PATHS:
        if os.path.isfile(p):
            vboxmanage_path = p
            return True
        if shutil.which(p):
            vboxmanage_path = p
            return True
    return False


def run_vbm(*args, timeout=15):
    if not vboxmanage_path:
        return "", "VBoxManage not found", -1
    try:
        r = subprocess.run(
            [vboxmanage_path] + list(args),
            capture_output=True, text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", -1
    except Exception as e:
        return "", str(e), -1


def parse_vm_list(output):
    vms = []
    for line in output.strip().splitlines():
        m = re.match(r'"(.+)"\s+\{([0-9a-f-]+)\}', line.strip())
        if m:
            vms.append({"name": m.group(1), "uuid": m.group(2)})
    return vms


def get_all_vms():
    out, _, _ = run_vbm("list", "vms")
    return parse_vm_list(out)


def get_running_set():
    out, _, _ = run_vbm("list", "runningvms")
    return {v["name"] for v in parse_vm_list(out)}


def get_vm_info(name):
    out, _, _ = run_vbm("showvminfo", name, "--machinereadable")
    info = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            info[k.strip()] = v.strip().strip('"')
    return info


def parse_metrics(output, vm_name):
    m = {
        "cpu_user": 0.0, "cpu_kernel": 0.0, "cpu_total": 0.0,
        "ram_used_mb": 0.0, "ram_total_mb": 0.0, "ram_percent": 0.0,
        "net_rx_kbps": 0.0, "net_tx_kbps": 0.0,
    }
    for line in output.splitlines():
        if vm_name not in line:
            continue
        def pct(l):
            x = re.search(r"([\d.]+)\s*%", l)
            return float(x.group(1)) if x else 0.0
        def kb(l):
            x = re.search(r"([\d.]+)\s*kB", l)
            return float(x.group(1)) / 1024 if x else 0.0
        def kbps(l):
            x = re.search(r"([\d.]+)\s*kB/s", l)
            return float(x.group(1)) if x else 0.0

        if "CPU/Load/User" in line:
            m["cpu_user"] = pct(line)
        elif "CPU/Load/Kernel" in line:
            m["cpu_kernel"] = pct(line)
        elif "RAM/Usage/Used" in line and "Total" not in line:
            m["ram_used_mb"] = kb(line)
        elif "RAM/Usage/Total" in line:
            m["ram_total_mb"] = kb(line)
        elif "Net/Rate/Rx" in line:
            m["net_rx_kbps"] += kbps(line)
        elif "Net/Rate/Tx" in line:
            m["net_tx_kbps"] += kbps(line)

    m["cpu_total"] = round(m["cpu_user"] + m["cpu_kernel"], 2)
    if m["ram_total_mb"] > 0:
        m["ram_percent"] = round(m["ram_used_mb"] / m["ram_total_mb"] * 100, 1)
    return m


# ── Background metrics collector ───────────────────────────────────────────────
def metrics_collector():
    initialized = set()
    while True:
        try:
            running = get_running_set()

            new_vms = running - initialized
            if new_vms:
                for name in new_vms:
                    run_vbm(
                        "metrics", "setup",
                        "--period", "1", "--samples", "3",
                        name,
                        "CPU/Load/User,CPU/Load/Kernel,RAM/Usage/Used,RAM/Usage/Total,Net/Rate/Rx,Net/Rate/Tx",
                    )
                time.sleep(2)
                initialized.update(new_vms)

            initialized &= running

            ts = time.time()
            new_cache = {}

            for name in running:
                try:
                    out, _, _ = run_vbm(
                        "metrics", "query", name,
                        "CPU/Load/User,CPU/Load/Kernel,RAM/Usage/Used,RAM/Usage/Total,Net/Rate/Rx,Net/Rate/Tx",
                    )
                    m = parse_metrics(out, name)
                    m["timestamp"] = ts
                    new_cache[name] = m
                    with cache_lock:
                        h = metrics_history[name]
                        h["cpu"].append(m["cpu_total"])
                        h["ram_percent"].append(m["ram_percent"])
                        h["net_rx"].append(m["net_rx_kbps"])
                        h["net_tx"].append(m["net_tx_kbps"])
                        h["timestamps"].append(ts)
                except Exception:
                    pass

            with cache_lock:
                metrics_cache.clear()
                metrics_cache.update(new_cache)

        except Exception:
            pass

        time.sleep(METRICS_INTERVAL)


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    return jsonify({"vboxmanage": vboxmanage_path or "not found"})


@app.route("/api/overview")
def api_overview():
    all_vms  = get_all_vms()
    running  = get_running_set()
    total_mb = 0
    total_cpu = 0
    for vm in all_vms:
        info = get_vm_info(vm["name"])
        total_mb  += int(info.get("memory", 0))
        total_cpu += int(info.get("cpus", 1))

    with cache_lock:
        avg_cpu = round(
            sum(m.get("cpu_total", 0) for m in metrics_cache.values()) / max(len(metrics_cache), 1),
            1,
        )
        total_ram_used = round(sum(m.get("ram_used_mb", 0) for m in metrics_cache.values()), 0)

    return jsonify({
        "total_vms":      len(all_vms),
        "running_vms":    len(running),
        "stopped_vms":    len(all_vms) - len(running),
        "total_cpus":     total_cpu,
        "total_memory_mb": total_mb,
        "avg_cpu_pct":    avg_cpu,
        "total_ram_used_mb": total_ram_used,
        "vboxmanage_ok":  bool(vboxmanage_path),
    })


@app.route("/api/vms")
def api_vms():
    all_vms = get_all_vms()
    running = get_running_set()
    result  = []

    for vm in all_vms:
        name     = vm["name"]
        is_run   = name in running
        info     = get_vm_info(name)

        net_count = sum(
            1 for i in range(8) if info.get(f"nic{i+1}", "none") != "none"
        )

        entry = {
            "name":       name,
            "uuid":       vm["uuid"],
            "status":     "running" if is_run else "stopped",
            "state":      info.get("VMState", "poweroff"),
            "os_type":    info.get("ostype", "Unknown"),
            "memory_mb":  int(info.get("memory", 0)),
            "cpus":       int(info.get("cpus", 1)),
            "vram_mb":    int(info.get("vram", 0)),
            "net_adapters": net_count,
            "description": info.get("description", "").strip(),
        }

        if is_run:
            with cache_lock:
                if name in metrics_cache:
                    entry["metrics"] = metrics_cache[name]
                h = metrics_history[name]
                if h["timestamps"]:
                    entry["history"] = {
                        "cpu":        list(h["cpu"]),
                        "ram_percent": list(h["ram_percent"]),
                        "net_rx":     list(h["net_rx"]),
                        "net_tx":     list(h["net_tx"]),
                        "timestamps": list(h["timestamps"]),
                    }

        result.append(entry)

    return jsonify(result)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not find_vboxmanage():
        print("WARNING: VBoxManage not found. Set VBOXMANAGE_PATHS in app.py.")
    else:
        print(f"VBoxManage: {vboxmanage_path}")

    t = threading.Thread(target=metrics_collector, daemon=True)
    t.start()

    print("\n  VirtualBox Monitor  →  http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
