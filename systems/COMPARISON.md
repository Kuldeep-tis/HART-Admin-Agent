# Agent speed on different computers

Built by `python scripts/system_benchmark.py` from the files in `systems/data/`. Fastest first at the top (avg time to first words on the 9 benchmark questions). Goal: first words in under 5 s with every answer right.

| # | Computer | CPU | RAM (free at start) | Model runs on | Power | Model | Right answers | Load | Read tok/s | Write tok/s | First words | Full reply | Chat first words | Chat full reply | Goal | Date | Report |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | lenovo-8gb<br>Lenovo V15-IIL | Intel(R) Core(TM) i5-1035G1 CPU @ 1.00GHz | 7.6 GB (3.7 GB) | CPU only | charger | qwen2.5:3b | 9/9 | 9.1 s | 25.6 | 4.8 | **10.7 s** | 17.1 s | 7.1 s | 11.9 s | ❌ | 2026-11-24 | [report](lenovo-8gb_2026-11-24_1119.md) |

**How to read it**

- **Read tok/s** decides how long the user waits before the first words (the model reads the prompt, tools and chat history first). **Write tok/s** decides how fast the answer appears.
- **Free RAM at start** matters: if the model doesn't fit in free RAM, the computer swaps and everything gets slower. Close the browser and other heavy apps before testing.
- **Power**: laptops on battery are often much slower than on the charger.
- **Model runs on**: Macs with Apple chips and PCs with an NVIDIA card run the model on the GPU, which is usually several times faster than CPU only.
