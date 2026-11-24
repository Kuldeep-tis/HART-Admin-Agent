"""Test the agent on THIS computer and save a report to systems/, so computers can be compared.

Run it once on every computer you want to try (8 GB laptop, 12 GB laptop, Mac, ...).
Each run:
  1. records the computer's details: CPU, RAM, GPU, OS, free RAM, battery/charger, Ollama
  2. measures the model's raw speed: load time, reading speed, writing speed (tokens/second)
  3. asks the 9 benchmark questions (right tool? right answer? how fast?)
  4. runs the 4 staff chats from chat_test.py (15 follow-up questions, timed)
  5. saves  systems/<computer>_<date>.md          (report for people)
            systems/data/<computer>_<date>.json   (numbers for the comparison)
  6. rebuilds systems/COMPARISON.md: every computer tested so far, fastest first

Usage (Ollama running, model pulled, same database files as the main machine):
    python scripts/system_benchmark.py                       # the model from .env
    python scripts/system_benchmark.py --name office-mac     # choose the computer's name
    python scripts/system_benchmark.py --models qwen2.5:3b granite4:3b
    python scripts/system_benchmark.py --quick               # skip the 15 chat questions
    python scripts/system_benchmark.py --compare-only        # only rebuild COMPARISON.md

Chats are kept in memory only: nothing is written to the databases.
"""

import argparse
import ctypes
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SYSTEMS_DIR = ROOT / "systems"
DATA_DIR = SYSTEMS_DIR / "data"
sys.path.insert(0, str(ROOT))

import benchmark_models as bm  # noqa: E402  (same folder: the 9 questions and their checks)
from chat_test import SCENARIOS  # noqa: E402  (the 4 multi-turn staff chats)
from helpers.power import power_mode, power_source, speed_warnings  # noqa: E402

GOAL_FIRST_WORDS_S = 5.0   # project goal: first words in under 5 seconds

# Fixed text for the raw speed test (~500 tokens), so every computer reads the same input.
SPEED_TEXT = " ".join(
    f"Rule {i}: Guests must check in after 14:00 and check out before 11:00. Late check-out until "
    f"14:00 costs half a night. Pets are allowed in garden rooms for a fee of 1000 rupees per night. "
    f"Cancellations within 7 days of arrival are non-refundable; earlier cancellations get a full "
    f"refund minus a 500 rupee fee. Breakfast is served from 07:00 to 10:30 in the main restaurant. "
    f"Airport transfers must be booked 24 hours ahead. Quiet hours are from 22:00 to 07:00."
    for i in range(1, 6)
)


# ---------------------------------------------------------------- computer details

def run_cmd(*cmd):
    """Output of a command, or "" if it doesn't exist on this computer."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def gb(n_bytes):
    return round(n_bytes / 1024 ** 3, 1)


def field(text, name):
    """The value after "name:" in command output like lscpu or system_profiler."""
    m = re.search(rf"^\s*{re.escape(name)}:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def memory():
    """RAM and swap in GB. "ram_free" = what apps can still use (the number that decides speed)."""
    system = platform.system()
    if system == "Linux":
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0]) * 1024
        return {"ram_total_gb": gb(info["MemTotal"]), "ram_free_gb": gb(info["MemAvailable"]),
                "swap_total_gb": gb(info["SwapTotal"]),
                "swap_used_gb": gb(info["SwapTotal"] - info["SwapFree"])}
    if system == "Darwin":
        vm = run_cmd("vm_stat")
        page = int(re.search(r"page size of (\d+)", vm).group(1)) if "page size" in vm else 16384

        def pages(name):
            m = re.search(rf"{name}:\s+(\d+)", vm)
            return int(m.group(1)) if m else 0

        free = (pages("Pages free") + pages("Pages inactive") + pages("Pages speculative")) * page
        swap = dict(re.findall(r"(total|used) = ([\d.]+)M", run_cmd("sysctl", "-n", "vm.swapusage")))
        return {"ram_total_gb": gb(int(run_cmd("sysctl", "-n", "hw.memsize") or 0)), "ram_free_gb": gb(free),
                "swap_total_gb": round(float(swap.get("total", 0)) / 1024, 1),
                "swap_used_gb": round(float(swap.get("used", 0)) / 1024, 1)}
    if system == "Windows":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                (n, ctypes.c_ulonglong) for n in ("total", "free", "page_total", "page_free",
                                                  "virtual_total", "virtual_free", "extended")]
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return {"ram_total_gb": gb(status.total), "ram_free_gb": gb(status.free),
                "swap_total_gb": gb(status.page_total - status.total),
                "swap_used_gb": gb((status.page_total - status.page_free) - (status.total - status.free))}
    return {}


def machine_name():
    system = platform.system()
    if system == "Linux":
        dmi = Path("/sys/devices/virtual/dmi/id")

        def read(name):
            try:
                value = (dmi / name).read_text().strip()
            except OSError:
                return ""
            return "" if value.lower() in ("", "none", "default string", "to be filled by o.e.m.") else value

        vendor = read("sys_vendor")
        # Lenovo keeps the readable name ("Lenovo V15-IIL") in product_version.
        name = read("product_version") if vendor.upper() == "LENOVO" else read("product_name")
        name = name or read("product_name")
        return name if vendor.lower() in name.lower() else f"{vendor} {name}".strip()
    if system == "Darwin":
        hw = run_cmd("system_profiler", "SPHardwareDataType")
        return f"{field(hw, 'Model Name')} ({field(hw, 'Model Identifier')})".strip()
    return platform.node()


def cpu():
    system = platform.system()
    if system == "Linux":
        info = run_cmd("lscpu")
        cores = int(field(info, "Core(s) per socket") or 0) * int(field(info, "Socket(s)") or 1)
        max_mhz = field(info, "CPU max MHz")
        return {"cpu": field(info, "Model name") or platform.processor(),
                "cpu_cores": cores or None, "cpu_threads": os.cpu_count(),
                "cpu_max_ghz": round(float(max_mhz) / 1000, 1) if max_mhz else None}
    if system == "Darwin":
        return {"cpu": run_cmd("sysctl", "-n", "machdep.cpu.brand_string"),
                "cpu_cores": int(run_cmd("sysctl", "-n", "hw.physicalcpu") or 0) or None,
                "cpu_threads": os.cpu_count(), "cpu_max_ghz": None}
    return {"cpu": platform.processor(), "cpu_cores": None, "cpu_threads": os.cpu_count(), "cpu_max_ghz": None}


def gpus():
    nvidia = run_cmd("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader")
    if nvidia:
        return [f"{line.strip()} (NVIDIA)" for line in nvidia.splitlines()]
    system = platform.system()
    if system == "Linux":
        return [re.sub(r"^\S+ [^:]+: ", "", line) for line in run_cmd("lspci").splitlines()
                if re.search(r"VGA|3D|Display", line)]
    if system == "Darwin":
        shown = run_cmd("system_profiler", "SPDisplaysDataType")
        cores = field(shown, "Total Number of Cores")
        return [f"{name}{f' ({cores} GPU cores)' if cores else ''}"
                for name in re.findall(r"Chipset Model:\s*(.+)", shown)]
    return []


def os_name():
    system = platform.system()
    if system == "Linux":
        try:
            release = Path("/etc/os-release").read_text()
            name = re.search(r'PRETTY_NAME="?([^"\n]+)', release).group(1)
        except (OSError, AttributeError):
            name = "Linux"
        return f"{name} (kernel {platform.release()})"
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    return platform.platform()


def collect_system(url):
    disk = shutil.disk_usage(ROOT)
    try:
        ollama = requests.get(f"{url}/api/version", timeout=10).json().get("version", "?")
    except (requests.RequestException, ValueError):
        ollama = "not reachable"
    return {"hostname": socket.gethostname(), "machine": machine_name(), **cpu(), "gpus": gpus(),
            **memory(), "disk_total_gb": gb(disk.total), "disk_free_gb": gb(disk.free),
            "power": power_source(), "power_mode": power_mode(), "os": os_name(),
            "python": platform.python_version(), "ollama": ollama}


# ---------------------------------------------------------------- measurements

def generate(url, payload, timeout=600):
    response = requests.post(f"{url}/api/generate", json={"stream": False, **payload}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def model_info(url, model):
    """Size and quantisation of the model file, e.g. "3.1B, Q4_K_M"."""
    try:
        details = requests.post(f"{url}/api/show", json={"model": model}, timeout=30).json().get("details", {})
        return {"parameters": details.get("parameter_size"), "quantization": details.get("quantization_level")}
    except (requests.RequestException, ValueError):
        return {}


def placement(url, model):
    """How much RAM the loaded model uses, and how much of it sits on the GPU."""
    try:
        for m in requests.get(f"{url}/api/ps", timeout=10).json().get("models", []):
            if model in (m.get("name"), m.get("model")):
                size, vram = m.get("size", 0), m.get("size_vram", 0)
                gpu_pct = round(100 * vram / size) if size else 0
                where = "CPU only" if gpu_pct == 0 else "GPU" if gpu_pct == 100 else f"{gpu_pct}% GPU, rest CPU"
                return {"model_ram_gb": gb(size), "gpu_percent": gpu_pct, "runs_on": where}
    except (requests.RequestException, ValueError):
        pass
    return {"model_ram_gb": None, "gpu_percent": None, "runs_on": "unknown"}


def raw_speed(url, model, num_ctx, keep_alive):
    """Hardware speed without our app: cold load time, then reading and writing speed.
    Uses the app's num_ctx so the model isn't reloaded for the agent questions afterwards."""
    bm.unload(url, model)
    options = {"num_ctx": num_ctx, "temperature": 0, "seed": 1}
    started = time.perf_counter()
    loaded = generate(url, {"model": model, "prompt": "hi", "keep_alive": keep_alive, "think": False,
                            "options": {**options, "num_predict": 1}})
    load_s = time.perf_counter() - started
    # The timestamp at the start makes sure nothing is reused from Ollama's cache.
    prompt = f"Run {datetime.now().isoformat()}.\n{SPEED_TEXT}\n\nList these hotel rules as short bullet points."
    out = generate(url, {"model": model, "prompt": prompt, "keep_alive": keep_alive, "think": False,
                         "options": {**options, "num_predict": 150}})

    def per_second(count, duration_ns):
        return round(count / (duration_ns / 1e9), 1) if count and duration_ns else None

    return {"cold_load_s": round(load_s, 1),
            "ollama_load_s": round((loaded.get("load_duration") or 0) / 1e9, 1),
            "read_tokens_per_s": per_second(out.get("prompt_eval_count"), out.get("prompt_eval_duration")),
            "write_tokens_per_s": per_second(out.get("eval_count"), out.get("eval_duration")),
            "words_per_s": round(per_second(out.get("eval_count"), out.get("eval_duration")) * 0.75, 1)
            if out.get("eval_count") else None}


def ask(agent, question, history):
    """One answer through the real agent code. Returns text, tool names, first-words and total seconds."""
    started, first = time.perf_counter(), None
    try:
        for event in bm.hotel_agent.respond_stream(agent, question, history):
            if event["type"] in ("token", "table") and first is None:
                first = time.perf_counter() - started
            if event["type"] == "final":
                reply = event["reply"]
                total = time.perf_counter() - started
                return reply.text, [c["name"] for c in reply.tool_calls], first or total, total
    except bm.hotel_agent.AgentError as exc:
        total = time.perf_counter() - started
        return f"ERROR: {exc}", [], first or total, total
    total = time.perf_counter() - started
    return "ERROR: no reply", [], first or total, total


def run_questions(agent, info):
    rows = []
    for question, want_tool, check, meaning in bm.build_questions():
        text, used, first, total = ask(agent, question, [])
        tool_ok = (want_tool in used) if want_tool else True
        fake = bm.invented_contact(text, info)
        answer_ok = not text.startswith("ERROR") and not fake and check(text.lower().replace(",", ""))
        rows.append({"question": question, "expected": meaning, "tools": used, "tool_ok": tool_ok,
                     "answer_ok": answer_ok, "made_up": fake, "first_s": round(first, 1),
                     "total_s": round(total, 1), "answer": text})
        print(f"  {'✅' if tool_ok and answer_ok else '❌'} {first:5.1f}s / {total:5.1f}s  {question}", flush=True)
    return rows


def run_chats(agent):
    """The chat_test.py scenarios, with history kept in memory (the app sends only the last few)."""
    rows = []
    for title, questions in SCENARIOS.items():
        history = []
        for question in questions:
            text, used, first, total = ask(agent, question, history)
            history += [{"role": "user", "content": question}]
            if not text.startswith("ERROR"):
                history += [{"role": "assistant", "content": text}]
            rows.append({"chat": title, "question": question, "tools": used, "first_s": round(first, 1),
                         "total_s": round(total, 1), "error": text.startswith("ERROR"), "answer": text})
            print(f"  {first:5.1f}s / {total:5.1f}s  [{title}] {question}", flush=True)
    return rows


def average(rows, key):
    values = [r[key] for r in rows]
    return round(sum(values) / len(values), 1) if values else None


def test_model(app, agent, model, info):
    cfg = app.config
    url = cfg["OLLAMA_URL"]
    cfg["OLLAMA_MODEL"] = model
    print(f"\n=== {model} ===", flush=True)

    print("  raw speed test (cold load, reading, writing)...", flush=True)
    raw = raw_speed(url, model, cfg["OLLAMA_NUM_CTX"], cfg["OLLAMA_KEEP_ALIVE"])
    print(f"  loaded in {raw['cold_load_s']}s | reads {raw['read_tokens_per_s']} tokens/s"
          f" | writes {raw['write_tokens_per_s']} tokens/s", flush=True)

    ask(agent, "hi", [])   # the app's prompt + tools read once (not counted), like the real warm-up
    where = placement(url, model)
    print(f"  model uses {where['model_ram_gb']} GB, runs on: {where['runs_on']}", flush=True)

    print("  9 benchmark questions (first words / total):", flush=True)
    questions = run_questions(agent, info)
    result = {"model": model, **model_info(url, model), **where, "raw": raw, "questions": questions,
              "summary": {"right_tool": sum(r["tool_ok"] for r in questions),
                          "right_answer": sum(r["answer_ok"] for r in questions),
                          "n_questions": len(questions),
                          "avg_first_s": average(questions, "first_s"),
                          "avg_total_s": average(questions, "total_s"),
                          "max_first_s": max(r["first_s"] for r in questions)}}
    if not app.config.get("BENCH_QUICK"):
        print(f"  {sum(len(q) for q in SCENARIOS.values())} chat questions (first words / total):", flush=True)
        chats = run_chats(agent)
        result["chats"] = chats
        result["summary"].update({"chat_avg_first_s": average(chats, "first_s"),
                                  "chat_avg_total_s": average(chats, "total_s"),
                                  "chat_errors": sum(r["error"] for r in chats)})
    bm.unload(url, model)
    return result


# ---------------------------------------------------------------- reports

def secs(value):
    return "–" if value is None else f"{value:.1f} s"


def short(text, n=160):
    text = " ".join(str(text).split()).replace("|", "/")
    return text if len(text) <= n else text[: n - 1] + "…"


def write_report(result, path):
    s = result["system"]
    max_ghz = f", up to {s['cpu_max_ghz']} GHz" if s.get("cpu_max_ghz") else ""
    lines = [
        f"# Agent test on {s['machine'] or s['hostname']}",
        "",
        f"**Date:** {result['date']}  ",
        f"**Computer name:** `{result['name']}` (hostname `{s['hostname']}`)  ",
        f"**Created by:** `python scripts/system_benchmark.py`",
        "",
        "## System configuration",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Machine | {s['machine'] or '–'} |",
        f"| CPU | {s['cpu']} |",
        f"| Cores / threads | {s['cpu_cores'] or '–'} / {s['cpu_threads'] or '–'}{max_ghz} |",
        f"| GPU | {', '.join(s['gpus']) or '–'} |",
        f"| RAM | {s.get('ram_total_gb', '–')} GB |",
        f"| **Free RAM when the test started** | **{s.get('ram_free_gb', '–')} GB** |",
        f"| Swap | {s.get('swap_total_gb', '–')} GB ({s.get('swap_used_gb', '–')} GB in use at start) |",
        f"| Disk | {s['disk_total_gb']} GB ({s['disk_free_gb']} GB free) |",
        f"| Power | {s['power']} |",
        f"| **Power mode** | **{s.get('power_mode', 'not recorded')}** |",
        f"| OS | {s['os']} |",
        f"| Python | {s['python']} |",
        f"| Ollama | {s['ollama']} |",
        "",
        "### App settings used",
        "",
        "| Setting | Value |",
        "|---|---|",
    ] + [f"| `{k}` | {v} |" for k, v in result["settings"].items()]

    for m in result["models"]:
        sm, raw = m["summary"], m["raw"]
        goal = "✅ yes" if sm["avg_first_s"] <= GOAL_FIRST_WORDS_S else "❌ no"
        lines += [
            "", f"## Model `{m['model']}` ({m.get('parameters') or '?'}, {m.get('quantization') or '?'})", "",
            "### Summary", "",
            "| Measure | Result |", "|---|---|",
            f"| Model runs on | {m['runs_on']} |",
            f"| Model RAM | {m['model_ram_gb']} GB |",
            f"| Cold load (model from disk into RAM) | {secs(raw['cold_load_s'])} |",
            f"| Reading speed (input) | {raw['read_tokens_per_s']} tokens/s |",
            f"| Writing speed (answer) | {raw['write_tokens_per_s']} tokens/s (about {raw['words_per_s']} words/s) |",
            f"| Right tool | {sm['right_tool']}/{sm['n_questions']} |",
            f"| Right answer | {sm['right_answer']}/{sm['n_questions']} |",
            f"| Avg first words (9 questions) | **{secs(sm['avg_first_s'])}** |",
            f"| Slowest first words (9 questions) | {secs(sm['max_first_s'])} |",
            f"| Avg full reply (9 questions) | {secs(sm['avg_total_s'])} |",
        ]
        if "chats" in m:
            lines += [f"| Avg first words (15 chat questions) | **{secs(sm['chat_avg_first_s'])}** |",
                      f"| Avg full reply (15 chat questions) | {secs(sm['chat_avg_total_s'])} |",
                      f"| Chat errors | {sm['chat_errors']} |"]
        lines += [f"| First words under {GOAL_FIRST_WORDS_S:.0f} s (goal) | {goal} |", "",
                  "### 9 benchmark questions", "",
                  "| ✓ | Question | Tool used | First words | Total | Expected | Answer |",
                  "|---|---|---|---|---|---|---|"]
        for r in m["questions"]:
            ok = "✅" if r["tool_ok"] and r["answer_ok"] else "❌"
            lines.append(f"| {ok} | {r['question']} | {', '.join(r['tools']) or '–'} | {secs(r['first_s'])} |"
                         f" {secs(r['total_s'])} | {short(r['made_up'] or r['expected'], 60)} | {short(r['answer'])} |")
        if "chats" in m:
            lines += ["", "### Staff chats (follow-up questions)", "",
                      "Not checked automatically: read the answers to judge them.", "",
                      "| Chat | Question | Tool used | First words | Total | Answer |",
                      "|---|---|---|---|---|---|"]
            for r in m["chats"]:
                lines.append(f"| {r['chat']} | {r['question']} | {', '.join(r['tools']) or '–'} |"
                             f" {secs(r['first_s'])} | {secs(r['total_s'])} | {short(r['answer'])} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_comparison():
    """One row per computer + model, newest run of each, fastest first words at the top."""
    runs = {}
    for file in sorted(DATA_DIR.glob("*.json")):
        result = json.loads(file.read_text(encoding="utf-8"))
        for m in result["models"]:
            runs[(result["name"], m["model"])] = (result, m, file.stem)   # sorted by name, so newest wins
    rows = sorted(runs.values(), key=lambda r: r[1]["summary"]["avg_first_s"])

    lines = [
        "# Agent speed on different computers",
        "",
        "Built by `python scripts/system_benchmark.py` from the files in `systems/data/`. "
        "Fastest first at the top (avg time to first words on the 9 benchmark questions). "
        f"Goal: first words in under {GOAL_FIRST_WORDS_S:.0f} s with every answer right.",
        "",
        "| # | Computer | CPU | RAM (free at start) | Model runs on | Power | Model | Right answers |"
        " Load | Read tok/s | Write tok/s | First words | Full reply | Chat first words | Chat full reply |"
        " Goal | Date | Report |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, (result, m, stem) in enumerate(rows, 1):
        s, sm, raw = result["system"], m["summary"], m["raw"]
        all_right = sm["right_answer"] == sm["n_questions"]
        goal = "✅" if sm["avg_first_s"] <= GOAL_FIRST_WORDS_S and all_right else "❌"
        lines.append(
            f"| {i} | {result['name']}<br>{s['machine']} | {s['cpu']} | {s.get('ram_total_gb')} GB"
            f" ({s.get('ram_free_gb')} GB) | {m['runs_on']} | {s['power']}<br>{s.get('power_mode', '–')} | {m['model']} |"
            f" {sm['right_answer']}/{sm['n_questions']} | {secs(raw['cold_load_s'])} |"
            f" {raw['read_tokens_per_s']} | {raw['write_tokens_per_s']} | **{secs(sm['avg_first_s'])}** |"
            f" {secs(sm['avg_total_s'])} | {secs(sm.get('chat_avg_first_s'))} | {secs(sm.get('chat_avg_total_s'))} |"
            f" {goal} | {result['date'][:10]} | [report]({stem}.md) |")
    lines += [
        "",
        "**How to read it**",
        "",
        "- **Read tok/s** decides how long the user waits before the first words (the model reads"
        " the prompt, tools and chat history first). **Write tok/s** decides how fast the answer appears.",
        "- **Free RAM at start** matters: if the model doesn't fit in free RAM, the computer swaps and"
        " everything gets slower. Close the browser and other heavy apps before testing.",
        "- **Power**: laptops on battery or in Power Saver mode are much slower (Power Saver made the 8 GB laptop 3× slower).",
        "- **Model runs on**: Macs with Apple chips and PCs with an NVIDIA card run the model on the GPU,"
        " which is usually several times faster than CPU only.",
    ]
    (SYSTEMS_DIR / "COMPARISON.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


# ---------------------------------------------------------------- main

def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "computer"


def main():
    parser = argparse.ArgumentParser(description="Test the agent on this computer and save a report to systems/.")
    parser.add_argument("--name", help="short name for this computer (default: its hostname)")
    parser.add_argument("--models", nargs="+", help="models to test (default: OLLAMA_MODEL from .env)")
    parser.add_argument("--quick", action="store_true", help="skip the 15 chat questions")
    parser.add_argument("--compare-only", action="store_true", help="only rebuild systems/COMPARISON.md")
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.compare_only:
        print(f"systems/COMPARISON.md rebuilt ({write_comparison()} rows)")
        return

    app = bm.create_app()
    app.config["LLM_PROVIDER"] = "local"
    app.config["BENCH_QUICK"] = args.quick
    url = app.config["OLLAMA_URL"]
    models = args.models or [app.config["OLLAMA_MODEL"]]

    # Check everything is ready before spending minutes on the test.
    try:
        installed = {m["name"] for m in requests.get(f"{url}/api/tags", timeout=10).json()["models"]}
    except (requests.RequestException, ValueError, KeyError):
        sys.exit(f"Ollama is not running at {url}. Start it (ollama serve, or open the Ollama app) and retry.")
    missing = [m for m in models if m not in installed and f"{m}:latest" not in installed]
    if missing:
        sys.exit("Model not installed: " + ", ".join(missing) + ". Run: " +
                 " && ".join(f"ollama pull {m}" for m in missing))

    system = collect_system(url)   # before loading any model, so "free RAM" means free for the model
    name = args.name or system["hostname"]
    print(f"Computer: {system['machine']} | {system['cpu']} | RAM {system.get('ram_total_gb')} GB"
          f" ({system.get('ram_free_gb')} GB free) | {system['power']} | power mode: {system['power_mode']}")
    for warning in speed_warnings(system["power_mode"], system["power"]):
        print(f"⚠ {warning} The results will not show this computer's real speed.")
    if system.get("ram_free_gb") is not None and system["ram_free_gb"] < 3:
        print("⚠ Less than 3 GB RAM free: close the browser and other heavy apps for a fair result.")

    with app.app_context():
        agent = next((r["agent"] for r in bm.agent_service.list_agents_with_hotels()
                      if r["agent"]["hotel_id"] == bm.HOTEL_ID), None)
        if agent is None:
            sys.exit("No agent for the Seaside Retreat hotel. Copy database/HART_Admin_Agent.db and "
                     "database/hotel_info.db from the main machine (see systems/README.md).")
        info = bm.hotel_tools.get_hotel_info(bm.HOTEL_ID)
        results = [test_model(app, agent, model, info) for model in models]

    cfg = app.config
    now = datetime.now()
    result = {
        "name": name, "date": now.strftime("%Y-%m-%d %H:%M"), "system": system, "models": results,
        "settings": {"OLLAMA_NUM_CTX": cfg["OLLAMA_NUM_CTX"], "OLLAMA_MAX_HISTORY": cfg["OLLAMA_MAX_HISTORY"],
                     "OLLAMA_KEEP_ALIVE": cfg["OLLAMA_KEEP_ALIVE"], "OLLAMA_TIMEOUT": cfg["OLLAMA_TIMEOUT"],
                     "temperature": bm.hotel_agent.qwen.TEMPERATURE,
                     "max output tokens": bm.hotel_agent.qwen.MAX_OUTPUT_TOKENS},
    }
    stem = f"{slug(name)}_{now.strftime('%Y-%m-%d_%H%M')}"
    (DATA_DIR / f"{stem}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(result, SYSTEMS_DIR / f"{stem}.md")
    rows = write_comparison()

    print(f"\nSaved systems/{stem}.md and systems/data/{stem}.json")
    print(f"systems/COMPARISON.md now compares {rows} computer/model runs.")
    for m in results:
        sm = m["summary"]
        print(f"  {m['model']}: {sm['right_answer']}/{sm['n_questions']} right, first words "
              f"{secs(sm['avg_first_s'])}, full reply {secs(sm['avg_total_s'])}"
              + (f", chats {secs(sm['chat_avg_first_s'])}" if "chat_avg_first_s" in sm else ""))


if __name__ == "__main__":
    main()
