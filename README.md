# GridWise

Production-ready HTTP API for LLM-assisted, deterministic 24-hour campus energy scheduling.

## Architecture

`request -> LLM -> JSON -> guardrails -> directives -> SciPy HiGHS LP -> replay validation -> response`

One OpenAI-compatible chat-completion call interprets all 1-3 notes. LLM output directly supplies optimizer constraints, but remains untrusted until `app/guardrails.py` validates type, fields, values, note order, and normalized hours. `app/optimizer.py` solves continuous minimum-cost dispatch and independently replays energy balance, solar/grid limits, battery transitions, rates, reserves, neutrality, and totals.

No phrase matcher or public-case special handling exists.

## Requirements

- Python 3.11+
- Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey)

Python libraries: FastAPI, Uvicorn, HTTPX, Pydantic, NumPy, SciPy/HiGHS, pytest.

## Local Run

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements-dev.txt
$env:LLM_API_KEY="YOUR_NEW_GEMINI_API_KEY"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Environment variables:

- `LLM_BASE_URL`: Gemini OpenAI-compatible base URL; default `https://generativelanguage.googleapis.com/v1beta/openai`
- `LLM_MODEL`: Gemini model; default `gemini-3.5-flash-lite`
- `LLM_API_KEY`: required Gemini API key
- `LLM_TIMEOUT_SECONDS`: provider timeout; default `8`

Never commit `.env` or credentials.

## API Tests

Health:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

Public sample-case test:

```bash
pytest -q tests/test_public_cases.py
```

Test loads all 10 cases from `BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json`, applies reference directive semantics, validates each 24-hour result, and compares optimal cost within `0.01` tolerance. Live LLM wording accuracy remains model-dependent.

## Automated Tests

```bash
pytest -q
```

Public-pack tests cover all 10 supplied scenarios, six directive types, multi-note cases, time windows, percentage conversion, optimizer constraints, 24-hour output, replay validation, and reference optimal costs. Test note interpretation against chosen live model before deployment because LLM behavior is model-specific.

## Docker Fallback

Build reproducibly without credentials in image:

```bash
docker build -t gridwise:local .
docker run --rm -p 8000:8000 \
  -e LLM_API_KEY="$LLM_API_KEY" \
  gridwise:local
```

Test `curl http://localhost:8000/health`. For remote provider, pass `LLM_API_KEY` only at runtime. Published registry image is not included because repository/registry credentials were unavailable; local tagged image is complete fallback artifact.

## Failure Behavior

- Invalid request: HTTP 422
- Malformed, unsafe, or unavailable LLM response: HTTP 502
- Valid directives with infeasible schedule: HTTP 422
- No raw provider response, stack trace, or secret appears in API errors

## Known Limitations

- Correct natural-language interpretation depends on selected model. Small local models may mishandle complex paraphrases; use stronger OpenAI-compatible model when needed.
- Battery model assumes 100% charge/discharge efficiency because input schema provides no efficiency values.
- No terminal value beyond required end-of-day energy neutrality.
- Service has no authentication or rate limiting; deploy behind managed API gateway for public production use.
- Public registry image and hosted URL require deployment account unavailable in local workspace.

## Three-Minute Demo Plan

1. Problem and six supported directives, 30 seconds.
2. Show one request crossing LLM, strict guardrails, LP, and replay validator, 60 seconds.
3. Run health and sample request; inspect interpretation, hourly balance, totals, and battery neutrality, 60 seconds.
4. Run `pytest -q`; show malformed-model and infeasible-schedule controlled failures, 30 seconds.
