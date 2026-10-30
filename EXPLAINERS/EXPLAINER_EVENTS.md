# How Events Work in Tau

An explainer for the Tau codebase — a minimal Python coding agent. This documents how events flow through the system, from the provider SDK all the way to the TUI.

## The big picture

Events flow in one direction, through a series of translation layers:

```text
Provider SDK (Anthropic/OpenAI/...)
   │  raw stream
   ▼
ProviderEvents (tau_ai/_provider_events.py)        ← private, provider-adapter level
   │  canonicalize_provider_stream()  (tau_ai/stream.py)
   ▼
AssistantMessageEvents (tau_agent/provider_events.py)  ← public provider contract
   │  run_agent_loop()  (tau_agent/loop.py)
   ▼
AgentEvents (tau_agent/events.py)                    ← portable agent brain
   │  CodingSession.prompt()  (tau_coding/session.py)
   ▼
CodingSessionEvents (tau_coding/events.py)           ← coding-app level
   ▼
TUI adapter (tau_coding/tui/adapter.py) → Textual widget state
```

The key design idea (from Pi, which Tau mirrors): **the agent layer emits events; frontends consume them**. The core never imports Textual or Rich.

## The four event levels

### 1. ProviderEvents (`tau_ai/_provider_events.py`)

Raw fragments from each SDK adapter: `response_start`, `text_delta`, `thinking_delta`, `tool_call`, `response_end`, `error`, `retry`. These are adapter-specific in shape.

### 2. AssistantMessageEvents (`tau_agent/provider_events.py`)

The *public* streaming contract every provider must speak (`stream_response()` returns these). This is the "canonical" stream:

```text
start → text_start/text_delta/text_end
      → thinking_start/thinking_delta/thinking_end
      → toolcall_start/toolcall_delta/toolcall_end
      → terminal: done | error
```

Key property: every delta event carries a `partial: AssistantMessage` — a **full snapshot of the message so far** (not just the diff). That is deliberate: subscribers can render or persist the complete state from any single event without keeping their own buffer.

Each provider adapter builds these differently; `canonicalize_provider_stream()` in `tau_ai/stream.py` normalizes them into this one shape (handling sequential vs. independent text/thinking channels).

### 3. AgentEvents (`tau_agent/events.py`)

What the agent loop emits — the heart of the app:

| Event | Meaning |
|---|---|
| `agent_start` / `agent_end` | whole run boundaries (`agent_end` carries the full message list) |
| `turn_start` / `turn_end` | one provider round-trip (`turn_end` carries the assistant message + tool results) |
| `message_start` / `message_update*` / `message_end` | lifecycle of each durable message (user, assistant, tool result) |
| `tool_execution_start` / `tool_execution_update*` / `tool_execution_end` | one tool call execution, with streaming partial results |

### 4. CodingSessionEvents (`tau_coding/events.py`)

App-level events layered on top of agent events:

- `SessionAgentEndEvent` — wraps `agent_end`, adds a `will_retry` flag
- `AgentSettledEvent` — the real "done" signal (compaction/retries may still run after `agent_end`)
- `QueueUpdateEvent` — pending steer/follow-up queue contents
- `CompactionStartEvent` / `CompactionEndEvent` — context-window compaction
- `AutoRetryStartEvent` / `AutoRetryEndEvent` — automatic retry after overflow compaction
- `EntryAppendedEvent`, `SessionInfoChangedEvent`, `ThinkingLevelChangedEvent`

The union type is `CodingSessionEvent = AgentEvent | SessionOwnEvent`.

All events are **Pydantic models** discriminated by a literal `type` field (e.g. `type AgentEvent = Annotated[... , Field(discriminator="type")]`), so consumers can do `if isinstance(event, X)` or switch on `event.type`.

## Message lifecycle in detail (loop.py)

`run_agent_loop()` drives the whole run as an async generator:

1. Emits `agent_start`, `turn_start`, then `message_start`/`message_end` for the user prompt.
2. Passes the transcript to `provider.stream_response()` and translates each `AssistantMessageEvent`:
   - `start` → `message_start`
   - every `partial` snapshot → `message_update` (with the nested event attached, so the TUI can pick out `text_delta`s)
   - final `message` → `message_end`
3. If the assistant message contains tool calls, emits `tool_execution_start` → (optional `tool_execution_update` streamed from the tool via its `on_update` callback) → `tool_execution_end`, then a `message_start`/`message_end` pair for the resulting `ToolResultMessage`.
4. Repeats the turn while the model keeps calling tools (or while new steering/follow-up messages are queued), then emits `turn_end` → `agent_end`.

Cancellation is cooperative: a `CancellationToken` is threaded through, the loop checks it before each turn and each tool call, and the harness synthesizes "Tool call interrupted by user" error results for dangling tool calls.

## End-to-end trace: "List the files in this repo"

The user types that; the model streams a sentence, calls the `bash` tool, gets results, and replies. (See the test `test_agent_loop_executes_tool_and_emits_tool_result_message_lifecycle` in `tests/test_agent_loop.py`, which covers exactly this pattern.)

**Turn 1 — model stream (no tool calls yet)**
```text
agent_start
turn_start
message_start   (UserMessage: "List the files in this repo")
message_end     (UserMessage)
message_start   (AssistantMessage: partial snapshot "")
message_update  (TextDeltaEvent "The repo contains...")   ← nested, with full partial
message_end     (AssistantMessage: "The repo contains...", stop_reason="toolUse")
```

**Turn 1 — tool execution**
```text
tool_execution_start   (tool_call_id="call-1", tool_name="bash", args={...})
tool_execution_update  (partial_result, streamed via on_update)      [optional]
tool_execution_end     (result=AgentToolResult, is_error=False)
message_start   (ToolResultMessage: "README.md  src/  tests/ ...")
message_end     (ToolResultMessage)
turn_end        (message=assistant, tool_results=[ToolResultMessage])
```

**Turn 2 — the model's final answer**
```text
turn_start
message_start   (AssistantMessage: "")
message_update  (TextDeltaEvent "Done.")
message_end     (AssistantMessage: "Done.", stop_reason="stop")
turn_end        (message="Done.", tool_results=[])
agent_end       (messages=[...all 5 messages...])
```

The transcript in the `messages` list is exactly: user prompt → assistant w/ tool call → tool result → assistant final. The `agent_end` payload snapshots all of it.

## What each consumer does per event

### The pull consumer — `CodingSession.prompt()`

Receives each event as it is yielded and:

- forwards everything unchanged to the TUI, **except** it swaps `agent_end` for `SessionAgentEndEvent(messages=..., will_retry=False)` (it inspects the assistant errors to decide `will_retry` for overflow compaction / provider failover)
- watches `MessageEndEvent` for the user message to trigger auto-naming the session in the background
- watches `ToolExecutionEndEvent` to invalidate the context-window token cache (tool output changes the token count)
- at the very end (in `finally`) calls `_reconcile_run_persistence` — the safety net that retries any message that failed to persist mid-run.

### The push subscribers — `AgentHarness._run()`

`AgentHarness._run()` calls `_notify(event)` before each `yield`. Two built-in subscribers:

- **Persistence** (`CodingSession._persist_on_message_end`): on every `message_end`, writes that message into the session JSONL file (keyed by `id(message)` for idempotent retries). This is why `message_start`/`message_end` are the "durable-message boundary" — transient `message_update` deltas never hit disk, only canonical completed messages.
- **Extensions** (`ExtensionRuntime._on_agent_event`): converts core events to extension-facing forms (e.g. `AgentStartEvent` resets a turn counter; text deltas feed status widgets); session-level events (`CompactionEndEvent`, etc.) are dispatched to handlers via `emit_event`.

### The TUI — `TuiEventAdapter.apply`

- `message_update` → appends `TextDeltaEvent.delta` to a live `assistant_buffer` / `add_thinking_delta`
- `message_end` (assistant) → **deletes all the provisional delta rows** (`del self.state.items[start:]`) and replaces them with the canonical message, so persisted block boundaries win
- `tool_execution_start` → adds a tool row; `tool_execution_update` → appends streaming output to it
- `agent_settled` → flushes and sets `running = False` (the real "done" signal)

## What actually gets persisted

One `SessionEntry` per message, in append-only JSONL (`tau_coding/session.py` → `MessageEntry`), plus entries for compaction, labels, model changes, thinking-level changes, branch summaries, etc. The message objects themselves are the same `WireModel` dataclasses the events carry — the session file is basically a replayable event log: restart Tau with `tau --resume` and the harness rebuilds the transcript by reading those entries back.

## Multi-turn queueing: `steer` vs `follow_up`

Inside a run, you can inject more user input without blocking the event stream:

- **`steer()`** — high priority. Appended to `_steering_queue`; drained *at the start of the next turn* (checked at the top of the loop). Typical use: the user presses Escape mid-run to interrupt, then rephrases the goal.
- **`follow_up()`** — checked *only after the model has no more tool calls* in a turn. Both queues are drained via `_drain_queue`, which respects `queue_mode`:
  - `"one_at_a_time"` (default) — pops one message per turn
  - `"all"` — empties the whole queue into the next turn

So the turn structure is:

```text
while True:
    while has_more_tools or pending(steering):
        run one provider turn (+ tools)
    follow_ups = drain follow_up queue
    if follow_ups: make them pending and continue
    else: break
```

Both queues are surfaced to the TUI as `QueueUpdateEvent`, so the UI can show "N queued messages" while the agent is busy.

## Mental model

Tau is an *event reducer*. Everything — text tokens, tool output, compaction, retries, queue changes — becomes a small typed event; then each layer (`tau_ai` → `tau_agent` → `tau_coding` → TUI) consumes the previous layer's events and re-emits a slightly richer event. The session file is just the same event stream, persisted at the durable-message granularity.

## File map

| File | Role |
|---|---|
| `src/tau_ai/_provider_events.py` | private raw provider event types |
| `src/tau_ai/stream.py` | `canonicalize_provider_stream` — normalizes adapters into canonical events |
| `src/tau_agent/provider_events.py` | canonical assistant stream contract (read this first) |
| `src/tau_agent/events.py` | agent-level events |
| `src/tau_agent/loop.py` | `run_agent_loop` — translates assistant events into agent events, runs tools |
| `src/tau_agent/harness.py` | stateful wrapper: `subscribe()`/`steer()`/`follow_up()`/`cancel()` |
| `src/tau_ai/fake.py` | `FakeProvider` — replays scripted event streams for tests |
| `src/tau_coding/events.py` | coding-session-level events |
| `src/tau_coding/session.py` | `CodingSession.prompt` — pull consumer, persistence subscriber |
| `src/tau_coding/extensions/runtime.py` | extension subscriber + `emit_event` |
| `src/tau_coding/tui/adapter.py` | TUI event adapter → display state |
| `tests/test_agent_loop.py` | end-to-end loop behavior tests (use `FakeProvider`) |