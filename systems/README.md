# Testing the agent on other computers

Every computer we test gets its own report in this folder:

| File | What it is |
|---|---|
| `systems/<computer>_<date>.md` | Report for one test run: system details, speeds, every answer |
| `systems/data/<computer>_<date>.json` | The same numbers in machine-readable form |
| `systems/COMPARISON.md` | All computers side by side, fastest first. Rebuilt after every run |

All of them are created by `scripts/system_benchmark.py`.

---

## 1. Set up the other computer (one time)

The app runs in a Python virtual environment (venv), and Ollama is installed directly on the computer (not in Docker).

### a) Install the tools

| | Linux | macOS | Windows |
|---|---|---|---|
| Git + Python 3.10+ | `sudo apt install git python3 python3-venv` (Ubuntu 20.04 has Python 3.8, which is too old: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.11 python3.11-venv`, then use `python3.11 -m venv .venv`) | `xcode-select --install` (Git) and Python from python.org or `brew install python` | git-scm.com and python.org |
| Ollama | `curl -fsSL https://ollama.com/install.sh \| sh` | Download the app from ollama.com | Download the installer from ollama.com |

Then download the model (about 2 GB):

```bash
ollama pull qwen2.5:3b
ollama pull granite4:3b     # optional: the backup model
```

### b) Get the code

```bash
git clone <repo-url> HART-Admin-Agent
cd HART-Admin-Agent
git checkout feature/local-llm-speed

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### c) Copy 3 files from the main machine

Git doesn't store these on purpose (secrets and data), so copy them over a USB stick, scp or a chat:

| File | Why |
|---|---|
| `.env` | Settings (`LLM_PROVIDER=local`, `OLLAMA_MODEL=qwen2.5:3b`, ...) |
| `database/hotel_info.db` | The sample hotel data |
| `database/HART_Admin_Agent.db` | The agent "Kia" and its prompt |

With the same files on every computer, the test is fair: same data, same agent, same settings.

---

## 2. Run the test

1. Plug in the charger (laptops on battery are slower).
2. Set the power mode to **Performance**. Power Saver made the 8 GB laptop about **3× slower** (first words 10.7 s instead of 4.4 s):
   - Ubuntu 22.04+: Settings → Power → Power Mode → Performance, or `powerprofilesctl set performance`
   - Ubuntu 20.04: no menu for it; keep the charger plugged in
   - Windows: Settings → System → Power → Power mode → Best performance
   - Mac: System Settings → Battery → Low Power Mode → Never
3. Close the browser and other heavy apps.
4. Run:

```bash
python scripts/system_benchmark.py --name office-mac     # pick a short name for this computer
```

The test takes about 3–8 minutes on a CPU-only laptop, and about 1–2 minutes on a Mac with an Apple chip or a PC with an NVIDIA card.

| Option | Does |
|---|---|
| `--name office-mac` | Name used in the file names and the comparison (default: the hostname) |
| `--models qwen2.5:3b granite4:3b` | Test several models one after another |
| `--quick` | Skip the 15 chat questions (only the 9 benchmark questions) |
| `--compare-only` | Only rebuild `COMPARISON.md` (e.g. after pulling other computers' reports) |

## 3. Collect the results

On the tested computer:

```bash
git add systems/
git commit -m "Add agent test results for office-mac"
git push
```

On the main machine, run `git pull`, then `python scripts/system_benchmark.py --compare-only`, and open `systems/COMPARISON.md`.

---

## What is measured

| Measure | Meaning |
|---|---|
| **Power mode** | Performance, balanced or power-saver. The test warns if Power Saver or battery is on |
| **Free RAM at start** | Memory the model can use. Too little means swapping, and everything gets slow |
| **Model runs on** | CPU only, or GPU (Apple chip / NVIDIA) |
| **Cold load** | Seconds to load the model from disk into memory (only after a restart) |
| **Read tok/s** | How fast the model reads the input. This decides the wait before the first words |
| **Write tok/s** | How fast the answer appears (1 token ≈ ¾ of a word) |
| **Right tool / right answer** | Out of the 9 benchmark questions (checked against the database) |
| **First words / full reply** | What the user feels, averaged over the 9 questions and the 15 chat questions |
| **Goal** | ✅ = first words under 5 s **and** every answer right |

## Why not Docker or a zip file?

- **Docker:** on a Mac, Ollama inside Docker can't use the Apple GPU, so it runs on the CPU only and is several times slower. Docker also takes RAM (about 1–2 GB on Mac and Windows), and on 8–12 GB machines that RAM is needed for the model. Docker is a good choice later for a real server, but not for comparing the speed of computers.
- **Zip:** it would include `.venv`, which only works on the computer it was made on, and the `.env` secrets. Git plus the 3 copied files is cleaner, and it also brings the results back (`git push`).
