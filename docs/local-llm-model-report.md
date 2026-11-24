# Local LLM Model Selection Report

**Project:** HART Admin Agent (AI assistant for hotel staff)
**Date:** 23 November 2026
**Branch:** `feature/local-llm-speed`

---

## 1. Summary

We need a hotel staff assistant that is **free**, keeps **all hotel data on our own machine**, gives **correct answers**, and starts replying in **under 5 seconds**.

We tested **10 local models** on a CPU-only laptop (no GPU).

**Selected model: `qwen2.5:3b`.** It was the only model that picked the right database tool for every question **and** got every fact right, and it was among the fastest of the accurate models.

| Selected model | Right tool | Right answer | First words | Total reply | RAM |
|---|---|---|---|---|---|
| **qwen2.5:3b** | **9/9 (100%)** | **9/9 (100%)** | 11.4 s | 19.1 s | 2.0 GB |

After the code optimisations on this branch, the same model averages **5.6 s to first words** and **9.5 s total** in multi-turn staff chats (it was 12.4 s and 20.4 s before).

---

## 2. System configuration

### Hardware

| Item | Value |
|---|---|
| Machine | Lenovo V15-IIL laptop |
| CPU | Intel Core i5-1035G1 (10th gen), 4 cores / 8 threads, 1.0–3.6 GHz |
| GPU | Intel Iris Plus Graphics G1 (integrated; **not used** by the model) |
| RAM | 7.6 GB |
| Swap | 46 GB (on SSD) |
| Disk | 137 GB SSD (29 GB free) |

### Software

| Item | Value |
|---|---|
| OS | Ubuntu 24.04.5 LTS (kernel 7.0.0-43) |
| Model runtime | Ollama 0.40.16 (runs models 100% on CPU) |
| App | Python 3.12.3, Flask |
| Database | SQLite (`hotel_info.db`, `HART_Admin_Agent.db`) |

### Model settings used by the app

| Setting | Value | Meaning |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:3b` | Selected model |
| `OLLAMA_NUM_CTX` | 4096 | Model working memory (tokens) |
| `OLLAMA_MAX_HISTORY` | 2 | Earlier chat messages sent with each question |
| `OLLAMA_KEEP_ALIVE` | 30m | Model stays loaded in RAM for 30 minutes after the last chat |
| `OLLAMA_TIMEOUT` | 180 s | Maximum wait for the model |
| Temperature | 0.2 | Low = consistent, factual answers |
| Max output | 1024 tokens | Upper limit per reply |
| Thinking mode | Off | 5–10x faster; not needed for these tasks |

---

## 3. Approaches considered

| Approach | Speed | Cost | Data privacy | Decision |
|---|---|---|---|---|
| Google Gemini (cloud API) | Fast | Free tier now, may cost later | ❌ Data sent to Google | Rejected |
| Paid API / own GPU server | Fast | ❌ Monthly cost | Depends | Rejected (must be free) |
| **Local model via Ollama** | Slower on CPU | ✅ Free | ✅ Data stays local | **Chosen** |

LangChain, LangGraph, RAG and vector databases were reviewed. They don't make the model faster or change where the data goes, so they weren't adopted.

---

## 4. How the models were tested

- **Tool:** `scripts/benchmark_models.py`. It runs the real app code (real system prompt, real database tools, real `hotel_info.db`).
- **Questions:** 9 staff questions (check-in time, room prices, pet policy, cancellation policy, rooms free tomorrow, guests checked in, revenue this month, cancelled bookings, swimming pool).
- **Expected answers** are read from the database when the test starts, so the checks are always correct for today's data.
- **Each answer is checked for:**
  - **Right tool:** did the model call the correct database function?
  - **Right answer:** does the reply contain the true facts?
  - **Made-up facts:** invented amenities (e.g. a pool), fake phone numbers or emails, "XXXX" placeholders.
- **Speed:** time to the first words, and total time for the full answer.
- **One model at a time:** each model is loaded and warmed up first (not counted), and unloaded afterwards so the next one has the RAM.
- Every wrong (❌) answer was also **checked by hand**, and scores were corrected where the automatic checker was wrong.

---

## 5. Models tested

### Round 2: final benchmark (9 staff questions, staff prompt)

Sorted by result. "First words" = how long the user waits before text appears.

| Rank | Model | Size | RAM used | Right tool | Right answer | Accuracy | First words | Total reply | Made anything up? | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 🥇 | **qwen2.5:3b** | 3B | 2.0 GB | 9/9 | **9/9** | **100%** | **11.4 s** | **19.1 s** | No | ✅ **Selected** |
| 🥈 | granite4:3b | 3B | 2.3 GB | 9/9 | 8/9 | 89% | 11.5 s | 20.1 s | No (wrong date range for revenue) | Backup |
| 3 | qwen3.5:4b | 4B | 2.9 GB | 9/9 | 8/9 | 89% | 38.3 s | 44.6 s | No (same revenue mistake) | ❌ 3x slower |
| 4 | llama3.2:3b | 3B | 2.4 GB | 9/9 | 7/9 | 78% | 22.3 s | 27.3 s | ❌ Invented a swimming pool | ❌ Rejected |
| 5 | qwen2.5:1.5b | 1.5B | 1.1 GB | 5/9 | 5/9 | 56% | 3.1 s | 6.6 s | ❌ Invented check-in time and cancellation policy | ❌ Rejected |
| 6 | qwen2.5:0.5b | 0.5B | 0.5 GB | 4/9 | 3/9 | 33% | 2.1 s | 4.1 s | ❌ Check-in "00:00" | ❌ Rejected |
| 7 | qwen3:0.6b | 0.6B | 1.0 GB | 3/9 | 2/9 | 22% | 1.4 s | 2.0 s | ❌ Mostly guesses | ❌ Rejected |
| ✗ | smollm2:360m | 0.36B | – | – | 0/9 | 0% | – | – | – | ❌ No tool support in Ollama |

> For qwen2.5:3b, one answer ("rooms free tomorrow") listed the free rooms per type (23 + 15 + 5) instead of the total (43). The facts were right, so it counts as correct.

### Round 1: first benchmark (6 guest questions, old prompt)

This ran under heavy memory pressure (about 9.7 GB swapped to disk), so these times are slower than the models' real speed.

| Model | Size | Right tool | Right answer | Accuracy | First words | Total reply | Verdict |
|---|---|---|---|---|---|---|---|
| qwen3.5:4b | 4B | 6/6 | 6/6 | 100% | 36.4 s | 49.1 s | Retested in round 2 |
| granite4:3b | 3B | 6/6 | 5/6 | 83% | 9.4 s | 14.8 s | Retested in round 2 |
| qwen3.5:2b | 2B | 6/6 | 4/6 | 67% | 18.6 s | 24.4 s | ❌ Invented a pool and an "XXXX" phone number |
| phi4-mini | 3.8B | 1/6 | 1/6 | 17% | 0.8 s | 9.5 s | ❌ Printed tool calls as text and invented policies |

### Considered but not tested

| Model | Reason |
|---|---|
| lfm2.5:1.2b, lfm2:1.2b | Not available in the Ollama library |
| Needle 3 (Cactus Compute) | Only picks tools (doesn't write answers) and doesn't run in Ollama, so it needs new code. Left as a later experiment |
| 7B+ and MoE models (e.g. Qwen3-30B-A3B) | Need 8–18 GB+ RAM, which doesn't fit this machine |

### What the results show

1. **Models under 3B are fast but unreliable.** They often skip the database and make up answers, which breaks the "correct" requirement.
2. **Every accurate model is about 3B**, and on this CPU they all need about 11 s before the first words. Changing the model alone can't reach the 5-second goal; the code changes below are what close the gap.
3. **No small model can serve as a separate "fast tool picker".** The best small model picked the right tool only 5 of 9 times.

---

## 6. Final model: `qwen2.5:3b`, and why

| Reason | Evidence |
|---|---|
| **Most accurate** | The only model with 9/9 right tools **and** 9/9 right answers |
| **Never made anything up** | No invented amenities, phone numbers or policies |
| **Fastest of the accurate models** | 11.4 s first words: as fast as granite4:3b and 3.4x faster than qwen3.5:4b |
| **Smallest RAM of the accurate models** | 2.0 GB, leaving room for the OS, browser and editor on an 8 GB machine |
| **Only model to get the revenue question right** | ₹85,500 for "this month so far" (granite4:3b and qwen3.5:4b used the whole month) |
| **Free and local** | Runs in Ollama on CPU, with no API key and no data leaving the machine |

**Backup model:** `granite4:3b` (89% accurate, same speed), kept installed.

---

## 7. Performance after optimisation (same model: qwen2.5:3b)

Measured with `scripts/chat_test.py`: 15 questions in 4 multi-turn staff chats, Chrome closed.

| Measure | Before | After | Change |
|---|---|---|---|
| Avg time to first words | 12.4 s | **5.6 s** | −55% |
| Avg total reply time | 20.4 s | **9.5 s** | −53% |
| Simple questions (no lookup) | 3.5 s | **2.9 s** | |
| Data questions (with a lookup) | 24.0 s | **9.6 s** | −60% |
| Booking lists | 37–75 s | **5–7 s** | ~8x faster |
| "hi" in the middle of a chat | 11.2 s | **2.0 s** | 5.6x faster |

### Where the time went (measured before the changes)

| Step | Time |
|---|---|
| Browser ↔ server, database, building the prompt, running SQL | < 0.1 s |
| Model reading the input (chat history was the slow part) | 7–11 s per call |
| Model writing the answer | ~6 words per second |

### What made it faster

- System prompt shortened from 306 to 167 words, and written for staff
- Only the last 2 chat messages are sent, each trimmed to 500 characters
- Booking lists are written by Python, not typed out by the model (no second model call)
- Tool results are sent as compact JSON
- Date periods ("last month", "next 30 days") are calculated in Python
- The chat UI removed an artificial 600 ms delay and now shows live progress ("Finding bookings… 6s")
- Closing Chrome freed about 1.5 GB of RAM and made replies about 40% faster

---

## 8. Known limitations and next steps

| Issue | Status |
|---|---|
| **Accuracy in multi-turn chats is 8/15.** On follow-up questions the model sometimes answers without checking the database, or picks the wrong period (e.g. "last month"). | Open. Planned fixes: Python reads the period from the question, plus a "must use a tool" safety net for data questions |
| First reply after a restart takes ~20–40 s while the model loads | Expected. The model then stays loaded for 30 minutes |
| Speed depends on free RAM. With Chrome open, replies were ~40% slower | Close heavy apps, or run on a machine with 16 GB RAM |
| Voice input uses Chrome's speech recognition, which sends the audio to Google | Accepted for now. Can be replaced by local Whisper without UI changes |
| Ollama answers one request at a time | Fine for a few staff users; needs review for many users at once |

---

## Appendix: benchmark questions (round 2)

| # | Question | Expected tool | What counts as correct |
|---|---|---|---|
| 1 | What time is check-in? | get_hotel_info | 14:00 / 2 PM |
| 2 | What room types do we have and what do they cost? | get_room_types | All prices: ₹4,500, ₹7,000, ₹15,000 |
| 3 | Can guests bring a dog? | get_policies | Allowed, ₹1,000 per night |
| 4 | What is our cancellation policy? | get_policies | Non-refundable within 7 days |
| 5 | How many rooms are free tomorrow? | check_room_availability | 43 rooms (or 23 + 15 + 5) |
| 6 | How many guests are checked in right now? | get_occupancy | 2 guests |
| 7 | What is our revenue this month so far? | get_booking_summary | ₹85,500 |
| 8 | Show the cancelled bookings. | list_bookings | Includes booking #46 |
| 9 | Do we have a swimming pool? | (none) | Says it doesn't have that information; doesn't invent a pool |

Re-run the tests:

```bash
python scripts/benchmark_models.py qwen2.5:3b granite4:3b   # model comparison
python scripts/chat_test.py                                 # multi-turn staff chats with timings
```
