# ADR-0007 — Compact chat memory by summary + facts + short window

**Status:** accepted (v4.1)

**Context.** Characters chat with owners (R63). Cost grows with prompt size; raw history is both expensive and a privacy liability. Telegram users expect long-lived relationships. Standard English BPE token heuristics (such as character count / 4 or word counts) fail drastically on Persian text, where multi-byte UTF-8 sequences tokenize at 1.0 to 1.5+ tokens per character, causing prompt budget overruns and cache misses.

**Decision.** Each session keeps only: persona card (≤ ~300 tokens), a rolling summary (≤ ~300), a facts list (≤ 20 one-line items) and the last ~8 raw turns. Compaction triggers under a **dual-condition gate**: either every `chat_compact_every_turns` (default 10) OR whenever `window_tokens >= chat_window_token_budget`. Compaction runs after the reply under a per-session lock: one cheap model call returns strict JSON `{summary, facts_add, facts_remove}` and leaving turns are dropped. Total summary + facts is capped by the plan's `chat_memory_tokens`.

**Persian Token BPE Calibration & Normalization:**
Standard English heuristics fail drastically on Persian text, where multi-byte UTF-8 sequences (2 bytes per character) tokenize at 1.0 to 1.4+ BPE tokens per character. To guarantee memory budgets never overflow Anthropic model limits:
1. **Conservative Token Heuristic:** Token estimation in `core/chat_memory.py` uses a calibrated upper-bound:
   $$\text{estimated\_tokens} = \max\left(1, \lceil \text{char\_count} \times 1.4 \rceil\right) = \max\left(1, \lceil \text{len}(\text{text.encode('utf-8')}) \times 0.70 \rceil\right)$$
2. **Prompt Caching Activation ($\ge 1,024$ Tokens Threshold):** Anthropic prompt caching requires at least 1,024 tokens (Claude 3.5 Sonnet / 3.0 Haiku) or 2,048 tokens (Claude 3.5 Haiku). A raw persona card (~300 tokens) + facts (~200 tokens) alone falls short (~500 tokens). To guarantee cache hits, the stable prefix consolidates:
   $$\text{Cacheable Prefix} = \text{Base System Instructions} + \text{Safety Rules} + \text{Persona Card} + \text{Durable Facts} + \text{Few-Shot Persona Dialogue}$$
   This deterministic prefix is engineered to reliably cross $\ge 1,024$ tokens, enabling 90% cache discounts and latency drops on ongoing turns.
3. **Unicode NFC & RTL Robustness:** All incoming dialogue and persona inputs are pre-normalized with `unicodedata.normalize('NFC', text)` to unify Persian/Arabic character variants (ی/ي, ک/ك, zero-width non-joiner `\u200c`). The compaction JSON parser uses regex block extraction (`r'\{[\s\S]*\}'`) with fallback repair to survive RTL formatting anomalies.

**Alternatives.** Full-history prompts — too costly. Vector retrieval — extra infrastructure and cost for small memories. Fine-tuning per character — out of scope. Character-length token heuristics — rejected due to Persian BPE token expansion.

**Consequences.** Predictable, low per-message cost, guaranteed prompt-cache hits, and deterministic context boundaries for Persian text.