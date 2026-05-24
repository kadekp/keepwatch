# Contributing to keepwatch

Thanks for considering a contribution. keepwatch is intentionally small — the goal is to keep it that way.

## Before you start

Please open an issue first if you're planning anything beyond a bug fix or a documentation tweak. Saves both of us time if the change isn't a fit for the project's scope.

In scope:
- Bug fixes
- New encoder profiles (see `detector/encoders.py`)
- New camera brand examples in `docs/cameras/`
- Dashboard improvements that stay vanilla JS + zero build step
- Performance work backed by measurements
- Documentation improvements

Out of scope (these will likely be declined):
- Integrations that introduce a new external service dependency
- Cloud sync, replication, off-host storage
- Multi-class detection beyond person (PR welcome only as a toggle, not a default)
- A web-based config editor
- A React/Vue/Svelte rewrite of the dashboard
- Anything that adds a service dependency (broker, queue, etc.)

If you're not sure, open an issue and ask. "Is this in scope?" is a fine question.

## Development setup

```bash
git clone https://github.com/<you>/keepwatch.git
cd keepwatch
uv sync --extra dev
uv run python -m unittest discover tests/
```

Tests must pass on Python 3.11+ on Linux and macOS (Windows isn't a supported runtime target — the recorder needs Unix-y signal handling, but feel free to PR if you've made it work).

## Code style

- Python: ruff defaults (`uv run ruff check .`). Format with `uv run ruff format .` before committing.
- Type hints on new functions. Don't go back and retrofit existing modules unless you're already touching them.
- Imports: stdlib → third-party → local, separated by blank lines. ruff sorts these automatically.
- JS: 4-space indent, no semi colon enforcement, no framework. Keep `dashboard/app.js` understandable by someone who's never used the project before.

## Commit messages

Conventional Commits format. Examples:

```
feat: add nvenc encoder profile
fix: handle empty segment list on event finalize
docs: clarify RTSP setup for EZVIZ in-app step
refactor: extract segment time math into helper
```

One logical change per commit. Squash before opening the PR if you have working-state commits.

## Tests

If you add a new encoder profile, add a corresponding test in `tests/test_encoders.py` and update `tests/test_recording.py` with the relevant `subTest` block.

If you add an API endpoint, add a `TestClient`-driven test in `tests/test_api_*.py`.

If you change motion detection, please attach measurements (FPS, CPU, false-positive rate on your own footage). A diff without numbers is hard to evaluate.

## PR checklist

Before requesting review:

- [ ] `uv run python -m unittest discover tests/` is green.
- [ ] `uv run ruff check .` is green.
- [ ] You haven't committed `.env`, `*.db`, or anything in `data/`, `logs/`, `media/`.
- [ ] If you added a public API field, you updated `docs/configuration.md`.
- [ ] If you added an encoder, you updated `docs/encoders.md`.
- [ ] Camera brand additions live in `docs/cameras/<brand>.example.md` — never with real credentials.

## License

By contributing, you agree your changes are licensed MIT alongside the rest of the project.
