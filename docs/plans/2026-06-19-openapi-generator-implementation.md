# OpenAPI Generator Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Replace the two stale OpenAPI helper drafts with one deterministic, schema-faithful, bounded, and safely writing generator.

**Architecture:** Generate from a local authenticated `make_app` fixture, normalize only the public Poe protocol path from exported Pydantic models, validate the complete public contract, then atomically write JSON and Markdown artifacts. Keep production request handling unchanged.

**Tech Stack:** Python 3.9+, FastAPI, Pydantic 2, pytest, Ruff, Black, GitHub Actions.

---

### Task 1: Add schema contract tests

**Files:**
- Create: `tests/test_generate_openapi_spec.py`
- Create: `tests/test_openapi_spec_mutations.py`

**Steps:**
1. Add failing tests for public-route filtering, request variants, authentication, operation IDs, response media types, and deterministic serialization.
2. Run `pytest -q tests/test_generate_openapi_spec.py tests/test_openapi_spec_mutations.py` and verify import/contract failures.
3. Add hostile mutations for missing security, leaked paths, changed operation IDs, missing request variants, and missing SSE output.

### Task 2: Implement the bounded generator

**Files:**
- Create: `docs/generate_openapi_spec.py`

**Steps:**
1. Build a no-network authenticated FastAPI fixture with documentation routes disabled.
2. Generate normalized request/response components from the exported Poe models.
3. Filter to the public route and assign deterministic metadata.
4. Validate route, security, schema, reference, and size invariants.
5. Add deterministic JSON and Markdown renderers.
6. Run the focused tests and verify they pass.

### Task 3: Harden output and CLI behavior

**Files:**
- Modify: `docs/generate_openapi_spec.py`
- Modify: `tests/test_generate_openapi_spec.py`

**Steps:**
1. Add failing tests for symlink, directory, duplicate-output, oversize, interrupted replace, and unexpected-error redaction.
2. Add no-follow path validation and same-directory private temporary files.
3. Add bounded writes, file and directory synchronization, cleanup, and concise CLI errors.
4. Run focused tests and verify all pass.

### Task 4: Document and gate generation

**Files:**
- Modify: `README.md`
- Modify: `.gitignore`
- Modify: `.github/workflows/lint.yml`

**Steps:**
1. Document location-independent generation and output options.
2. Ignore only the generated artifacts.
3. Set read-only workflow permissions and disable checkout credential persistence.
4. Run Ruff, Black, Pyright, Actionlint, and the full pytest suite.

### Task 5: Audit and land the stack

**Files:**
- Review all changed files.

**Steps:**
1. Run dependency, current-tree, and full-history credential scans without printing values.
2. Push a consolidation PR referencing #2 and #3.
3. Wait for exact-head hosted checks and inspect failures.
4. Merge only after required evidence is green, close superseded PRs, and verify the final branch SHA and zero open PRs.
