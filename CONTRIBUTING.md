# Contributing to cove-core

Thanks for your interest. cove-core is the open-source base of Lucid Cove — a self-hostable production and family intelligence system. This guide covers how to work with the codebase.

## Code of conduct

Be respectful, assume good faith, and keep it about the work. Harassment or discrimination of any kind is not welcome. Maintainers may remove contributions or contributors that break this.

## Ground rules

- **Code is Apache 2.0.** By contributing, you agree your contributions are licensed under Apache 2.0.
- **The Canon is sacred.** The 22 Lucid Principles, the Manifesto, and the Tuning Keys are quoted exactly and never generated, paraphrased, rearranged, or extended. Do not submit changes that alter Canon text or generate Canon-like content. Canon content is CC BY 4.0, not Apache.
- **Protected files — do not modify without maintainer discussion.** The Lucid Tuning Protocol implementation and the tuning-key data are locked; changes must conform to the protocol spec and are reviewed by a maintainer, never merged from a routine PR:
  - `src/utils/quantum.py` — centralized quantum entropy (the single implementation)
  - `src/graphs/ltp_graph.py` — the LTP tuning pipeline
  - `src/dashboard/routes/tuning_request.py` — the Tune Now endpoint
  - the runtime tuning-key library (`lt_reference.json`) and the Canon tuning keys — protected as source data
  - PRs touching these files will be asked to justify the change against the protocol spec before review.
- **Config-driven, not hardcoded.** The repo is the single source; an instance is only its overrides. Don't hardcode hostnames, domains, ports, paths, user names, or family names — route them through config (`cove.yaml`) or environment (see `.env.example`). New deployment values go in config, with a neutral default.
- **Clean is the moat.** Do the specific things everyone needs, nothing more. The base stays lean; extras are opt-in on top. Every feature should answer: *can another family run this?*
- **New feature area → new file.** Don't grow large existing files; create a new module.

## Architecture in one paragraph

The container image is generic and carries no source. Code is mounted at runtime and merged (`/cove-core/src` + an optional `/overlay/src` → `/app/src`). Configuration layers `defaults < cove.yaml.example < your instance`. The dashboard is FastAPI + a scheduler; agents are config-defined; the Lucid Tuning Protocol lives in the separate `ltp-core` package and the public Drop.

## Development

```bash
git clone https://github.com/LucidPrinciples/lucid-cove.git
cd lucid-cove
pip install -e ".[dev]"     # installs pytest, ruff
python -m pytest            # run the test suite
ruff check .                # lint
```

To run a full stack locally, follow `QUICKSTART.md`.

## How contributions land (the branch-and-PR model)

`main` is protected — nobody pushes to it directly. All work happens on a branch and lands through a reviewed pull request:

1. Branch from `main` (`dev/<your-handle>/<topic>`). Never commit to `main`.
2. Build, then run the same gates every internal change runs: the test suite and the cleanliness guard, green before you open the PR.
3. Open a PR against `main`. A maintainer reviews and merges — merging is always a human review act, which is what keeps the base clean and the protected files safe.

This is the same pipeline the project uses internally: an agent or maintainer working *inside* a Cove hands off a patch through the same review queue an outside contributor's PR goes through. One flow, both directions.

## Pull requests

- Keep PRs focused. One concern per PR.
- Run `pytest` and `ruff check` before submitting.
- Don't commit secrets. `.env` and instance config are gitignored — keep it that way.
- Describe what changed and why. If it changes config or env, update `.env.example` and the relevant `.example` files.
- Touching a protected file (see Ground rules)? Say so in the PR and explain how the change conforms to the protocol spec.

## Security

Please do not file public issues for security problems. See `SECURITY.md` for how to report responsibly.
