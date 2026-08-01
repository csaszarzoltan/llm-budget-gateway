# Task-oriented Unified Console 9.3

Version 9.1 improves repeated day-to-day work without changing existing center APIs.

## What changed

- Six daily workflow cards start from user goals rather than service names.
- Symptom search is available from `GET /v1/console/workflows?q=429`.
- Recent tasks and favorites reduce repeated navigation.
- Tenant and selected workspace context persist locally between visits.
- The API runner validates that non-GET request bodies are JSON objects before sending.
- Invalid JSON receives an inline, focusable, screen-reader-announced error.

## Privacy and storage

The console stores only non-sensitive navigation preferences in browser `localStorage`: tenant label, selected workspace, recent workspace/capability pairs, favorites, and theme. Bearer keys remain in `sessionStorage`. Request bodies, results, prompts, and secrets are not added to recent-task or favorite preferences.

## Workflow catalog

The initial workflows cover first protected request, spend investigation, 412/429/502 recovery, key rotation, release preparation, and security review. They compose existing capabilities and do not duplicate domain logic.

## Compatibility

All existing gateway and center routes remain unchanged. `/v1/console/workflows` is additive. The existing universal runner and center catalog remain available for expert users.


## Guided stepper

Selecting **Start** opens the runner with the first capability in the workflow. The guided panel shows the ordered steps, current step, and previous/next controls. It composes existing APIs and does not automatically execute consequential actions.


## Guided input presets

Every step loads a non-secret example JSON object and explains the purpose of the request. The console labels it as example data and never submits it automatically. Replace representative models, metrics, regions, and thresholds with values appropriate to the current tenant before selecting **Send request**.

### Resumable progress and safer request feedback (9.4)

Guided workflows now persist non-secret progress in browser local storage. Cards show not-started, in-progress, or completed state; Resume returns to the last active step; completed steps are marked and directly selectable; and progress can be reset. A successful guided request marks its current step complete but never advances or submits another step automatically.

The runner disables Send while a request is active, exposes `aria-busy`, applies a 30-second client timeout, and adds a suggested next action for common HTTP failures. Local usage counters record allow-listed event names and integer counts only. Progress and counters never contain tenant IDs, keys, bodies, prompts, results, or response content.
