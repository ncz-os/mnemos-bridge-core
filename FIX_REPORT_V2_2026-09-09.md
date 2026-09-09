# MNEMOS Bridge Core Audit Fix Report V2

Date: 2026-09-09

Branch: `fix/audit-findings-v2`

Base inspected: `origin/master` at `966b41e4a0261b321a99ed1ae6707996faaaa8e5`

## Existing code findings

### H1: retry logic and idempotency

The current default-branch implementation in
`src/mnemos_bridge_core/dispatch.py` was already fail-closed:

- `dispatch()` has an explicit `idempotent: bool = False` keyword.
- It computes `max_retries = max(0, retries) if idempotent else 0`.
- A transient timeout, `httpx.ConnectError`, `httpx.ReadError`, or selected
  retryable MCP transport error is therefore surfaced after the first failed
  attempt unless the caller explicitly passes `idempotent=True`.
- `Dispatcher` also defaults to `idempotent=False` and forwards an explicit
  per-call override.

No production retry-code change was needed or made. The existing regression
only counted calls from a client that always raised `ReadError`; it did not
model a mutation committed before the response failed. That regression was
strengthened to track the committed side effect independently from the call
count and assert that both remain exactly one.

### H2: plaintext transport handling

On the current default branch, `McpClient.__init__()` stored the supplied URL
without validating its scheme. `from_url()` and `open_from_url()` passed that
URL through unchanged. Therefore, this fresh base did not contain the reported
HTTPS-only rejection; it instead accepted plaintext HTTP silently and had no
explicit security boundary or LAN escape-hatch contract.

## Changes made

### `src/mnemos_bridge_core/client.py`

- Added `_validate_url()` so URL validation occurs during construction, before
  `sse_client()` can perform network I/O or receive the bearer token.
- HTTPS is accepted by default. Plaintext HTTP is rejected by default.
  Unsupported schemes and URLs without a host are rejected even when the
  insecure opt-in is enabled.
- Added the explicit `allow_insecure_transport` keyword to
  `McpClient.__init__()`, `McpClient.from_url()`, and
  `McpClient.open_from_url()`.
- Added the explicit `MNEMOS_ALLOW_INSECURE_TRANSPORT` environment escape hatch
  for existing sibling bridges whose current public connection APIs do not
  forward the new keyword. Values `1`, `true`, `yes`, and `on`, matched
  case-insensitively, enable it. Explicitly passing
  `allow_insecure_transport=False` overrides the environment.
- Plaintext opt-in emits a `UserWarning` that labels the mechanism as trusted
  LAN-only.
- Added `_safe_endpoint()` so the warning includes only scheme, host, and port,
  never URL userinfo, path, query, or fragment values.
- Malformed URL parsing raises a generic error with suppressed exception
  chaining so secret-bearing invalid-port text cannot reappear in formatted
  tracebacks.
- When `allow_insecure_transport` is omitted, `from_url()` and
  `open_from_url()` omit the new keyword when delegating. This preserves
  compatibility with subclasses that override the previous constructor or
  factory signatures while still allowing the base constructor to evaluate
  the environment escape hatch.
- Preserved the existing task-affine owner-task lifecycle in
  `open_from_url()`, including startup failure cleanup and cross-task close
  behavior.

### `tests/test_dispatch.py`

- Replaced the generic non-retry test with a partial-mutation regression that
  increments a separate committed-side-effect counter before raising
  `httpx.ReadError`.
- Added `_make_mcp_error()` as a test-only compatibility helper for both older
  MCP releases that provide `MCPError.from_error_data()` and MCP 1.30, which
  constructs `MCPError` directly. This was needed because the declared project
  dependency range is `mcp>=1.0,<3` and the available real test environment
  uses MCP 1.30.

### `tests/test_client_url_validation.py`

Added URL-policy, connection-path, environment-precedence, secret-redaction,
and subclass-compatibility regression coverage.

### `README.md`

Documented HTTPS as the default, `allow_insecure_transport=True` and
`MNEMOS_ALLOW_INSECURE_TRANSPORT=1` as explicit escape hatches, explicit
`False` precedence, and the trusted-LAN-only restriction.

## Regression tests added or strengthened

- `test_dispatch_does_not_retry_non_idempotent_tool_after_partial_mutation`
- `test_plaintext_http_is_rejected_by_default`
- `test_plaintext_http_is_rejected_before_network_io`
- `test_plaintext_lan_connection_works_with_explicit_opt_in`
- `test_plaintext_lan_connection_accepts_environment_opt_in`
- `test_explicit_false_overrides_environment_opt_in`
- `test_non_http_schemes_are_rejected_even_with_insecure_opt_in`
- `test_url_without_host_is_rejected`
- `test_insecure_transport_warning_redacts_url_secrets`
- `test_malformed_url_error_traceback_redacts_url_secrets`
- `test_from_url_preserves_legacy_subclass_constructor_signature`
- `test_open_from_url_preserves_legacy_subclass_factory_signature`

## Real test-suite results

The first attempted `uv run --extra dev pytest` did not collect tests because
the sandbox has no DNS/network access and no pre-existing environment existed
in this checkout. The empty environment it created was moved out of the
checkout. The complete suite was then run with an already-installed Python
3.13 environment containing MCP 1.30.0 and pytest 9.1.1:

```text
$ /private/tmp/mnemos-reconcile.zGRxNz/mnemos/.venv/bin/python -m pytest -q -rs
..........................................s............................. [ 98%]
.                                                                        [100%]
=========================== short test summary info ============================
SKIPPED [1] tests/test_mcp_client_smoke.py:16: MNEMOS_TEST_BASE is not set
72 passed, 1 skipped in 0.41s
```

Focused dispatch, URL-validation, and client regression run:

```text
$ /private/tmp/mnemos-reconcile.zGRxNz/mnemos/.venv/bin/python -m pytest -q tests/test_dispatch.py tests/test_client_url_validation.py tests/test_client.py
...................................                                      [100%]
35 passed in 0.52s
```

`git diff --check` completed with no errors. Ruff import fixes and formatting
were applied to the touched Python files. The only remaining Ruff diagnostic is
the pre-existing intentional `BaseException` catch in the task-affine lifecycle
at `src/mnemos_bridge_core/client.py`; the same diagnostic is present on
`origin/master`.

The independent re-review verdict was `APPROVE` with no blocking findings.
