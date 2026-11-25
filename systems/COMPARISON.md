# Agent speed on different computers

Built by `python scripts/system_benchmark.py` from the files in `systems/data/`. Fastest first at the top (avg time to first words on the 9 benchmark questions). Goal: first words in under 5 s with every answer right.

| # | Computer | CPU | RAM (free at start) | Model runs on | Power | Model | Right answers | Load | Read tok/s | Write tok/s | First words | Full reply | Chat first words | Chat full reply | Goal | Date | Report |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | mac-mini-m4<br>Mac mini (Mac16,10) | Apple M4 | 16.0 GB (3.7 GB) | GPU | charger<br>balanced | qwen2.5:3b | 9/9 | 0.7 s | 559.1 | 46.9 | **0.8 s** | 1.6 s | 0.7 s | 1.4 s | ✅ | 2026-11-25 | [report](mac-mini-m4_2026-11-25_1149.md) |
| 2 | lenovo-12gb<br>Lenovo IdeaPad S145-15IIL | Intel(R) Core(TM) i5-1035G1 CPU @ 1.00GHz | 11.5 GB (7.6 GB) | CPU only | charger<br>– | qwen2.5:3b | 9/9 | 3.5 s | 97.2 | 13.7 | **3.7 s** | 6.4 s | 3.0 s | 5.0 s | ✅ | 2026-11-24 | [report](lenovo-12gb_2026-11-24_1301.md) |
| 3 | lenovo-8gb<br>Lenovo V15-IIL | Intel(R) Core(TM) i5-1035G1 CPU @ 1.00GHz | 7.6 GB (2.2 GB) | CPU only | charger<br>performance | qwen2.5:3b | 9/9 | 6.5 s | 96.1 | 15.1 | **4.4 s** | 7.3 s | 2.8 s | 5.3 s | ✅ | 2026-11-24 | [report](lenovo-8gb_2026-11-24_1225.md) |
| 4 | mac-desktop-i5<br>Mac Pro (MacPro7,1) | Intel(R) Core(TM) i5-10400 CPU @ 2.90GHz | 16.0 GB (8.2 GB) | CPU only | charger<br>unknown | qwen2.5:3b | 9/9 | 3.0 s | 83.1 | 7.9 | **4.9 s** | 9.5 s | 3.5 s | 6.9 s | ✅ | 2026-11-24 | [report](mac-desktop-i5_2026-11-24_1844.md) |
| 5 | TIS-79<br>Lenovo IdeaPad S145-15IIL | Intel(R) Core(TM) i5-1035G1 CPU @ 1.00GHz | 11.5 GB (5.5 GB) | CPU only | battery<br>– | qwen2.5:3b | 9/9 | 23.6 s | 52.3 | 8.5 | **5.1 s** | 8.9 s | 3.8 s | 6.3 s | ❌ | 2026-11-24 | [report](tis-79_2026-11-24_1218.md) |

**How to read it**

- **Read tok/s** decides how long the user waits before the first words (the model reads the prompt, tools and chat history first). **Write tok/s** decides how fast the answer appears.
- **Free RAM at start** matters: if the model doesn't fit in free RAM, the computer swaps and everything gets slower. Close the browser and other heavy apps before testing.
- **Power**: laptops on battery or in Power Saver mode are much slower (Power Saver made the 8 GB laptop 3× slower).
- **Model runs on**: Macs with Apple chips and PCs with an NVIDIA card run the model on the GPU, which is usually several times faster than CPU only.
