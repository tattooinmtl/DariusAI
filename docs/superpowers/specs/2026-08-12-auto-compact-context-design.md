# Technical Design Spec: Auto Compact Context

**Date**: 2026-08-12  
**Status**: Approved  
**Target Area**: `src/dariusai/agent/chat.py`, `src/dariusai/viz/server.py`, `src/dariusai/viz/static/index.html`  

---

## 1. Executive Summary

As chat sessions grow during long interactive development tasks, conversation history accumulates tokens until prompt size approaches the model's maximum context window limit.

The **Auto Compact Context** feature provides automatic threshold-based compaction and manual user-triggered compaction to compress conversation history, truncate bloated tool output blocks, and retain key context while freeing up prompt space.

---

## 2. Architectural Design

```
+-------------------------------------------------------------------+
|                        Web UI (index.html)                        |
|  - Displays Token Stats & Compact Button [⚡ Compact]             |
|  - Handles `context_compacted` events and shows notification      |
+-------------------------------------------------------------------+
                                 |
                         WebSocket Connection
                                 |
+-------------------------------------------------------------------+
|                     Server API (viz/server.py)                    |
|  - Handles WS action `{"type": "compact"}`                        |
|  - Invokes `session.compact(force=True)`                          |
+-------------------------------------------------------------------+
                                 |
+-------------------------------------------------------------------+
|                      ChatSession (agent/chat.py)                  |
|  - `auto_compact_enabled: bool = True`                            |
|  - `compact_threshold_ratio: float = 0.75`                        |
|  - `keep_recent_turns: int = 6`                                   |
|  - `compact(force: bool = False)` method                          |
|  - Tool output truncation + LLM conversation summarization        |
+-------------------------------------------------------------------+
```

---

## 3. Detailed Component Specifications

### 3.1 Backend: `ChatSession` (`src/dariusai/agent/chat.py`)

1. **Configuration**:
   - `auto_compact_enabled: bool = True`
   - `compact_threshold_ratio: float = 0.75`
   - `keep_recent_turns: int = 6`

2. **Compaction Method (`compact(force: bool = False)`)**:
   - Checks if compaction is needed: `force or (auto_compact_enabled and context_window > 0 and current_input_tokens >= context_window * compact_threshold_ratio)`.
   - Returns early if context size is below threshold and `force=False`, or if message count is too small ($\le \text{keep\_recent\_turns} + 2$).

3. **Compaction Pipeline**:
   - **Step 1: Tool Output Truncation**: Scans messages older than `keep_recent_turns`. If a `tool_result` content string exceeds 500 characters, it is truncated to `"[Output truncated for context compaction: X chars omitted]..."`.
   - **Step 2: Summarization Prompt**: Sends older turns (before `keep_recent_turns`) to `llm.complete()` with prompt:
     > *"Summarize the prior conversation history concisely. Preserve: 1) Active task/goal, 2) Files created/edited, 3) Important decisions, 4) Current state."*
   - **Step 3: History Reconstruction**:
     - `messages = [{"role": "user", "content": "[Prior Context Summary]:\n" + summary}, {"role": "assistant", "content": "Understood. Context compacted. Proceeding with task."}] + recent_turns`
   - **Step 4: Event Emission**: Emits `context_compacted` event payload:
     `{"type": "context_compacted", "old_tokens": ..., "new_tokens": ..., "saved_tokens": ...}`.

---

### 3.2 WebSocket & UI API (`src/dariusai/viz/server.py` & `index.html`)

1. **WebSocket Handler (`server.py`)**:
   - Receives `{"type": "compact"}` message.
   - Triggers `session.compact(force=True)`.

2. **UI Controls (`index.html`)**:
   - Next to token stats footer (`TPS: 12.4 · 150,000 / 200,000 tok`), renders a `[⚡ Compact]` button.
   - Listens for `context_compacted` events and displays a subtle banner/toast in the chat panel:
     `⚡ Context Compacted: 154,200 tok → 14,100 tok (-90.8%)`.

---

## 4. Validation & Test Plan

1. `tests/test_chat_compact.py`:
   - Unit test for threshold auto-compaction triggering.
   - Unit test for manual compaction invocation (`force=True`).
   - Unit test for tool output truncation in older turns.
   - Unit test for message list reconstruction and preservation of recent turns.
   - Integration test for WebSocket `"compact"` message handling.

---

## 5. Security & Risk Analysis

- **State Loss Risk**: `keep_recent_turns` ensures recent immediate task context (such as file paths and active function calls) is never lost.
- **LLM Failure Fallback**: If the summarization LLM call fails, tool truncation is still performed without crashing the chat session.
