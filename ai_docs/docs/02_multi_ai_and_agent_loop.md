# 02 – Multi-AI Providers & the Agent Loop

How Text-to-Printer talks to Gemini, ChatGPT and Claude, and how the generate → compile → fix → verify loop decides when a design is good enough.

Key files: [`providers.py`](../../src/core/ai/providers.py), [`validation_loop.py`](../../src/core/ai/validation_loop.py), [`prompts.py`](../../src/core/ai/prompts.py), [`json_repair.py`](../../src/core/ai/json_repair.py), [`mesh_validator.py`](../../src/core/slicer/mesh_validator.py).

## 1. Choosing a provider

1. Put the matching key in `.env` (see [`.env.example`](../../.env.example)):

   | Provider | `active_provider` values | `.env` key |
   |---|---|---|
   | Google Gemini | `gemini` | `GEMINI_API_KEY` |
   | OpenAI | `chatgpt` (alias `openai`) | `OPENAI_API_KEY` |
   | Anthropic | `claude` (alias `anthropic`) | `ANTHROPIC_API_KEY` |

2. Edit [`inputs/configs/ai_models.json`](../../inputs/configs/ai_models.json):

   ```json
   {
     "active_provider": "gemini",
     "models": { "gemini": "<model id>", "chatgpt": "<model id>", "claude": "<model id>" },
     "reasoning_effort": { "chatgpt": "high" }
   }
   ```

3. Restart the service (the provider is created per generation via `get_active_ai_provider()`).

Behaviour to know:
- A missing key raises `ValueError("Active AI provider is '<x>', but <KEY> is not set in .env.")`; an unknown provider name raises a `ValueError` listing `gemini, chatgpt, claude`.
- Model names come from the `models` map (lookup by the configured name, then canonical name, then a built-in default). Use model IDs that exist on your account.
- Each provider implements `generate_content(prompt, system_instructions) -> str` and is asked for JSON:
  Gemini uses `response_mime_type="application/json"`; OpenAI uses `response_format={"type":"json_object"}` plus `reasoning_effort` (retried without it if the call fails); Anthropic uses `max_tokens=8192` and joins the text blocks.
- If `ai_models.json` is missing or unparsable, built-in defaults are used (a warning is logged).

## 2. Prompts

Files in [`inputs/ai_prompts/`](../../inputs/ai_prompts/):

| File | Used for |
|---|---|
| [`ai_instructions.txt`](../../inputs/ai_prompts/ai_instructions.txt) | System prompt: design rules (centred on X/Y, flat on Z=0, single top-level `union()`, 0.1 mm overlaps, ≥1.2 mm walls, overhangs ≤55° preferred, mm units, max 245 mm per side, vanilla OpenSCAD only, `$fn` at top) and the required JSON keys: `filename`, `code`, `color_hierarchy`, `expected_parts_count` |
| [`error_prompt.md`](../../inputs/ai_prompts/error_prompt.md) | Fix pass. Placeholders: `{user_request}`, `{broken_code}`, `{size}`, `{error_log}`, `{geometry_errors}` |
| [`verify_prompt.md`](../../inputs/ai_prompts/verify_prompt.md) | Verify pass. Placeholders: `{user_request}`, `{generated_code}`, `{size}`, `{print_time}`, `{filament_grams}`, `{geometry_health}` |

For the fix and verify roles, `_build_agent_instructions` keeps the design rules from `ai_instructions.txt` (everything before `Output Requirements:`) and replaces the output section so the AI returns only `{"code": ...}`. For edits, `_build_initial_prompt` includes earlier user requests, the latest code, and its size/time/grams, then the new request; active time/size constraints are appended as `DESIGN CONSTRAINTS`.

## 3. Agent levels

`agent_level` in [`agent_config.json`](../../inputs/configs/agent_config.json) (shipped value `2`; code fallback `3` if the key is absent) controls how much self-review runs *after* a clean compile:

| Level | After a clean first generation | After a clean fix | After a verify pass changes the code |
|---|---|---|---|
| 1 | done | done | on problems: stop (keeps last clean/compiled result) |
| 2 | verify | done | problems → fix pass |
| 3 | verify | verify | problems → fix pass |
| 4 | verify | verify | clean change → verify again (until calls run out) |

Compile errors and geometry/constraint problems **always** trigger a fix pass at every level.

## 4. The loop (`generate_with_validation`)

```
GENERATE → evaluate ──problems──▶ ERROR_FIX → evaluate ──┐
              │ clean                                     │ (repeat)
              ▼                                           ▼
           VERIFY (if level allows) ──same code──▶ DONE
```

- **Budget**: every AI call (generate, fix, verify) costs one call; the loop stops at `max_calls`. The default is `max_ai_calls` (3) or the per-service value from `service_limits`.
- **Evaluate** (`_evaluate_candidate_code`): (1) compile with OpenSCAD (60 s timeout; error text is fed back verbatim, as is an "empty or missing STL" message); (2) read bounding size, slice to a temporary 3MF for print time and grams; (3) if `check_geometry_health`, run `analyze_mesh_geometry` (part count vs `expected_parts_count`, grounding within `bed_contact_threshold_mm`, joints thinner than `min_connection_thickness_mm`); (4) check constraints.
- **Geometry feedback**: factual messages (e.g. "Part #1 starts at Z=… in mid-air") are inserted into `{geometry_errors}` of the fix prompt; they are also summarised in `{geometry_health}` for verify.
- **Constraint errors**: print time over `max_print_time_minutes`, or largest dimension over `max_size_inches` (`0` = unlimited). An unknown print time (slicing failed) is always an error: *"Slicing failed. The geometry may be non-manifold, too thin, or outside the build volume."*
- **Verify**: the AI returns the same code to accept, or improved code, which is re-evaluated before being accepted.
- **If OpenSCAD is not installed** the compile step is skipped and the code is treated as valid (`_validate_code` returns success).
- **Result**: the last *clean* candidate wins; otherwise the last candidate that compiled (with its issues listed in `warnings`); otherwise the raw code with warning `"Model failed to compile."`. The STL/3MF are moved to `output_dir/<file_basename>.*` when provided.
- **API outage resilience**: if the AI call fails during a fix or verify pass and an earlier candidate exists, the loop stops and returns it with a warning (`"…recovered previous working design."`). A failure on the very first generation is raised to the caller.
- The generator yields `{"type": "progress", "attempt", "max_calls", "message"}` events and a final `{"type": "result", ...}` (code, filename, colors, warnings, stl_path, 3mf_path, size, time, grams). Helpers `generate_openscad_validated` and `generate_openscad_code` wrap it.

## 5. Per-service limits and constraints

In [`agent_config.json`](../../inputs/configs/agent_config.json) (shipped values):

| Setting | Value | Meaning |
|---|---|---|
| `max_ai_calls` | 3 | Default call budget |
| `service_limits` | `online_backend` 3, `telegram_bot` 3, `cli` 3, `google_tasks` 5 | Per-front-end budget (`get_max_ai_calls(service)`) |
| `service_constraints.<service>` | `max_print_time_minutes: 0`, `max_size_inches: 0` | Per-service caps; `0` disables. Top-level `max_print_time_minutes` / `max_size_inches` are used when no service is passed |
| `check_geometry_health` | true | Enable mesh analysis |
| `min_connection_thickness_mm` / `bed_contact_threshold_mm` | 1.2 / 0.4 | Mesh-check thresholds |
| `google_tasks_enable_supports` | false | Default supports for Google Tasks |
| `session_retention` | see [01](01_system_architecture.md) | Storage tiers |

Service names used in code: `online_backend`, `telegram_bot`, `cli`, `google_tasks`. Constraints are looked up by the `service` argument passed to the loop (the web backend and CLI pass it explicitly).

## 6. Robust JSON parsing (`json_repair.py`)

`parse_ai_response` is tolerant of imperfect model output, in order:
1. Take the text between the first `{` and last `}`; `json.loads` it.
2. On failure, `_try_repair_json` re-escapes the raw `code` value (newlines, quotes, backslashes) and retries, including trimming stray closing braces.
3. Regex extraction of `code`, `filename`, `color_hierarchy`, `expected_parts_count`; or a Markdown ```` ```openscad ```` block; or plain non-JSON text as code.
4. Last resort: a code stub containing the raw output in a comment, which then fails to compile and goes to the fix pass.

`clean_scad_code` strips Markdown fences and unescapes literal `\n` sequences. Defaults: `filename` = `gemini_part`, `expected_parts_count` = 1.

## 7. Circuit breaker

The AI loop itself has **no** circuit breaker; it is bounded only by `max_calls`. The circuit breaker lives in the process supervisor, which stops restarting a crashing service (see [01](01_system_architecture.md)).

## Related docs
- [01 – System Architecture](01_system_architecture.md)
- [03 – Web Interface Guide](03_web_interface_guide.md)
- [04 – Telegram Bot Guide](04_telegram_bot_guide.md)
- [05 – Google Tasks Bot Guide](05_google_tasks_bot_guide.md)
- [06 – Slicing & 3MF Pipeline](06_slicing_and_3mf_pipeline.md)
- [07 – Mesh Geometry Validation](07_mesh_geometry_validation.md)
- [08 – Bambu & Safety Guide](08_bambu_and_safety_guide.md)
