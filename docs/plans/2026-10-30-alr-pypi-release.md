# alr PyPI Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A version tag publishes alr to PyPI, after the owner approves, so a user installs with `uv tool install alr` and updates with `uv tool upgrade alr`.

**Architecture:** A new workflow, `.github/workflows/release.yaml`, runs on a version tag. Its `build` job runs the existing `build/build.sh`, which is the only way the wheel gets its version, and hands `dist/` to a `publish` job that waits in the `pypi` environment for the owner's approval, uploads with the PyPA publish action under trusted publishing (no stored token), and creates a GitHub release with the wheel attached. An rc tag goes to TestPyPI as a prerelease; a final tag goes to PyPI. Before any of that, the repository gets its license and real package metadata, because PyPI freezes them per version.

**Tech Stack:** GitHub Actions, Docker (via `build/build.sh`), `pypa/gh-action-pypi-publish`, `gh`, `uv`. Local checks: `actionlint` from its Docker image, `uv version --dry-run`, `./build/build.sh` (the main session runs it; subagents never run Docker).

**Spec:** `docs/design/02_architectural_decisions.md`, ADR-028. Read it first; it decides the design and this plan only orders the work.

## Global Constraints

- Package name `alr`; fallback `alr-cli` only if PyPI refuses `alr` at the first upload (owner's call at that point, not in this plan). The command stays `alr` either way.
- License: MIT, copyright holder `Jeremy K. Johnson`, year 2026. Metadata lands before the first rc tag.
- Tag shapes: final `v[0-9]+.[0-9]+.[0-9]+`, release candidate `v[0-9]+.[0-9]+.[0-9]+rc[0-9]+`. An rc tag publishes to TestPyPI and makes a GitHub prerelease; a final tag publishes to PyPI and makes a normal GitHub release. Both wait for approval in the GitHub environment `pypi`.
- The wheel is built only by `./build/build.sh`. `build.sh` and `build/Dockerfile` are not changed.
- Trusted publishing: the `publish` job holds `id-token: write` and `contents: write`, nothing else; the `build` job holds `contents: read`. No token is created or stored anywhere.
- Every action is pinned to a commit digest with the tag in a trailing comment, per the repository's build rule (tools from pinned images, nothing installed on the runner):
  - `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`
  - `actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2`
  - `actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2`
  - `pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086 # v1.14.3`
- Workflow lint, locally only: `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7 -color`. CI does not lint workflows; the first rc tag is the live test.
- Docs that say `uv tool install alr` (wiki Install page, README Install section) are committed in this branch but the wiki is pushed only after the first final tag is on PyPI, so the docs are never ahead of the registry. The README change ships in the same PR and is accepted as a short window.
- Branch: `feature/pypi-release`, created from `develop` by the main session before Task 1, carrying the ADR-028 commit `8d23582` that sits on local `develop`; local `develop` is then reset to `origin/develop`. Subagents never `git push`.
- Code shape for any shell (`~/.claude/rules/shell/shell.md`): `printf` not `echo`, `"${var}"` quoting, one guard per `if`. Code shape for Markdown: casual wiki tone (`~/.claude/projects/.../memory/wiki-casual-tone.md`), exact flag names and commands kept verbatim.

## Review Focus

Inputs the spec implies that no obvious test covers, most likely first. Each has its check in the task that owns it.

1. An rc tag's version string, such as `0.2.1rc1`, must be accepted by `uv version` inside the Docker build, or the rehearsal fails before it starts (Task 1, dry-run check).
2. An rc tag has no `CHANGELOG.md` section, so the release notes extraction returns nothing; the GitHub prerelease must still be created with a stand-in body, not fail (Task 2, notes step).
3. A re-run of the `publish` job after a partial failure must not fail on "file already exists": PyPI accepts an identical re-upload, and `gh release create` must skip when the release already exists (Task 2, idempotence step).
4. The two upload steps must be mutually exclusive on the tag shape: a final tag must never reach TestPyPI and an rc tag must never reach PyPI (Task 2, `if:` conditions, actionlint).
5. The `publish` job must not build or check out source beyond `CHANGELOG.md`, so a compromised build step cannot reach the publishing credential (Task 2, permissions and step list).

---

### Task 1: License and package metadata

**Files:**
- Create: `LICENSE`
- Modify: `pyproject.toml` (the `[project]` table)
- Modify: `CHANGELOG.md` (the `[Unreleased]` section)

**Interfaces:**
- Produces: the metadata PyPI will show. Task 2's workflow uploads whatever `build.sh` produces from this.

- [ ] **Step 1: Create `LICENSE`**

The MIT license text, verbatim, with the copyright line `Copyright (c) 2026 Jeremy K. Johnson`.

- [ ] **Step 2: Add metadata to `pyproject.toml`**

In `[project]`, keep `name`, `version`, `readme`, `requires-python`, `dependencies` as they are, and set:

```toml
description = "Run a task list through Claude Code, one fresh session per task"
license = "MIT"
license-files = ["LICENSE"]
authors = [{ name = "Jeremy K. Johnson" }]
keywords = ["claude-code", "agent", "task-runner", "tui"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "Operating System :: POSIX",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Software Development :: Build Tools",
]

[project.urls]
Repository = "https://github.com/twistingmercury/agentic_loop_runner"
Documentation = "https://github.com/twistingmercury/agentic_loop_runner/wiki"
Changelog = "https://github.com/twistingmercury/agentic_loop_runner/blob/develop/CHANGELOG.md"
```

`license = "MIT"` is the PEP 639 SPDX form; it needs `setuptools>=77`. Raise `[build-system] requires` to `["setuptools>=77"]`. No email in `authors`; the owner's address is not published.

- [ ] **Step 3: Check the metadata parses and the rc version form is accepted**

Run:

```sh
uv run python -c "import tomllib; p = tomllib.load(open('pyproject.toml','rb'))['project']; print(p['license'], p['authors'], p['urls']['Repository'])"
uv version --dry-run 0.2.1rc1
```

Expected: the first prints `MIT [{'name': 'Jeremy K. Johnson'}] https://github.com/twistingmercury/agentic_loop_runner`; the second prints `alr 0.0.1 => 0.2.1rc1` and changes nothing.

- [ ] **Step 4: Add the changelog entry**

Under `## [Unreleased]`, add an `### Added` section (before the existing `### Changed`) with:

```markdown
- Releases are published to PyPI. A version tag builds the wheel, waits for
  the owner's approval, and uploads it, so `uv tool install alr` and
  `uv tool upgrade alr` are the install and update commands. A release
  candidate tag such as `v0.3.0rc1` publishes to TestPyPI instead. The wheel
  is also attached to a GitHub release for the tag.
- A `LICENSE` file (MIT) and the package metadata PyPI shows: description,
  author, keywords, classifiers, and links to the repository, wiki, and
  changelog.
```

- [ ] **Step 5: Run the unit tests and lint**

Run: `make test`
Expected: all tests pass, ruff clean. (The main session runs `./build/build.sh` after Task 2; it covers the Docker build with the new build-system floor.)

- [ ] **Step 6: Commit**

```bash
git add LICENSE pyproject.toml CHANGELOG.md
git commit -s -m "Add the MIT license and the package metadata PyPI shows"
```

---

### Task 2: The release workflow

**Files:**
- Create: `.github/workflows/release.yaml`

**Interfaces:**
- Consumes: `./build/build.sh`, which writes `dist/alr-<version>-py3-none-any.whl` and `dist/alr-<version>.tar.gz`, taking `<version>` from `git describe --tags --abbrev=0`.
- Produces: on an rc tag, a TestPyPI upload and a GitHub prerelease; on a final tag, a PyPI upload and a GitHub release. Task 3's runbook drives it.

- [ ] **Step 1: Write the workflow**

`name: Release`. Trigger:

```yaml
on:
  push:
    tags:
      - "v[0-9]+.[0-9]+.[0-9]+"
      - "v[0-9]+.[0-9]+.[0-9]+rc[0-9]+"
```

Job `build` (`runs-on: ubuntu-latest`, `permissions: {contents: read}`):

1. `actions/checkout` with `fetch-depth: 0` (so `git describe` sees the tag) and `persist-credentials: false`.
2. `run: ./build/build.sh`.
3. `actions/upload-artifact` with `name: dist`, `path: |\n dist/alr-*.whl\n dist/alr-*.tar.gz`, `if-no-files-found: error`.

Job `publish` (`needs: build`, `runs-on: ubuntu-latest`, `environment: pypi`, `permissions: {id-token: write, contents: write}`):

1. `actions/download-artifact` with `name: dist`, `path: dist/`.
2. `actions/checkout` with `sparse-checkout: CHANGELOG.md`, `sparse-checkout-cone-mode: false`, `persist-credentials: false`. This is the only source the job sees.
3. Step `Publish to TestPyPI`, `if: contains(github.ref_name, 'rc')`, the PyPA action with `repository-url: https://test.pypi.org/legacy/`.
4. Step `Publish to PyPI`, `if: ${{ !contains(github.ref_name, 'rc') }}`, the PyPA action with no `with:`.
5. Step `Write release notes`, a `run:` block that sets `VERSION="${GITHUB_REF_NAME#v}"` and extracts the changelog section:

   ```sh
   awk -v version="${VERSION}" '
       $0 == "## [" version "]" || index($0, "## [" version "] ") == 1 { found = 1; next }
       /^## \[/ { found = 0 }
       found { print }
   ' CHANGELOG.md > notes.md
   if [ ! -s notes.md ]; then
       printf "Release candidate %s. Published to TestPyPI for rehearsal; not on PyPI.\n" "${GITHUB_REF_NAME}" > notes.md
   fi
   ```

6. Step `Create the GitHub release`, a `run:` block with `env: {GH_TOKEN: ${{ github.token }}}`:

   ```sh
   if gh release view "${GITHUB_REF_NAME}" >/dev/null 2>&1; then
       printf "release %s already exists; skipping\n" "${GITHUB_REF_NAME}"
       exit 0
   fi
   prerelease=""
   case "${GITHUB_REF_NAME}" in
       *rc*) prerelease="--prerelease" ;;
   esac
   gh release create "${GITHUB_REF_NAME}" --title "${GITHUB_REF_NAME}" --notes-file notes.md ${prerelease} dist/alr-*
   ```

   The `publish` job does no other checkout and runs no build. Upload to the index happens before the GitHub release, so a release never exists for a version the index rejected.

- [ ] **Step 2: Lint the workflow**

Run: `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7 -color`
Expected: no output, exit 0. (The main session runs this; it is Docker.)

- [ ] **Step 3: Check the notes extraction against the real changelog**

Run, from the repository root:

```sh
VERSION=0.2.0 awk -v version="$VERSION" '$0 == "## [" version "]" || index($0, "## [" version "] ") == 1 { found = 1; next } /^## \[/ { found = 0 } found { print }' CHANGELOG.md | head -3
VERSION=0.2.1rc1 awk -v version="$VERSION" '$0 == "## [" version "]" || index($0, "## [" version "] ") == 1 { found = 1; next } /^## \[/ { found = 0 } found { print }' CHANGELOG.md | wc -l
```

Expected: the first prints the blank line and `### Added` that open the 0.2.0 section; the second prints `0`, which is the case the stand-in body covers.

- [ ] **Step 4: Check the two upload steps cannot both run**

Read the two `if:` lines. One is `contains(github.ref_name, 'rc')`, the other its negation. A tag `v0.2.1rc1` matches the first only; `v0.2.1` matches the second only. actionlint in Step 2 catches a malformed expression.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yaml
git commit -s -m "Add the release workflow: tag to TestPyPI or PyPI after approval"
```

---

### Task 3: Docs and the owner's runbook

**Files:**
- Modify: `README.md` (add an `## Install` section after the Table of Contents entry list and before `## Usage`; add `- [Install](#install)` to the Table of Contents)
- Modify: `../agentic_loop_runner.wiki/Install.md` (the wiki clone; separate repository)
- Modify: `../agentic_loop_runner.wiki/Contributing.md` (the "Branches and pull requests" section)
- Create: `docs/plans/2026-10-30-alr-pypi-release-runbook.md`

**Interfaces:**
- Consumes: the commands the workflow makes true: `uv tool install alr`, `uv tool upgrade alr`.

- [ ] **Step 1: README Install section**

Casual tone, matching the wiki. Content:

~~~markdown
## Install

You need [uv](https://docs.astral.sh/uv/) and the `claude` CLI on your PATH.

```sh
uv tool install alr
```

To update later:

```sh
uv tool upgrade alr
```

Each release is also on the [releases page](https://github.com/twistingmercury/agentic_loop_runner/releases) as a wheel, and the wiki's [Install](https://github.com/twistingmercury/agentic_loop_runner/wiki/Install) page has the details. To build from source instead, see [Contributing](https://github.com/twistingmercury/agentic_loop_runner/wiki/Contributing).
~~~

- [ ] **Step 2: Wiki Install page**

Rewrite `Install.md` so the Steps section is: install with `uv tool install alr`, check with `alr --version` (keep the existing version-line explanation, with `0.2.1` as the example once that is the first PyPI version, or whatever the first final tag is), update with `uv tool upgrade alr`, remove with `uv tool uninstall alr`. Drop Docker and Git-for-build from Requirements; keep Python 3.12, uv, the `claude` CLI, Git for commit actions, and Linux or macOS. Keep the PATH note. Move the clone-and-build steps (checkout tag, `make build`, `make install`) into a new `## Building from source` section on `Contributing.md`, placed before "Branches and pull requests", with one sentence saying that is the contributor path and PyPI is the user path.

- [ ] **Step 3: Wiki Contributing release text**

In "Branches and pull requests", after "The project owner pushes branches and tags releases.", add:

```markdown
A tag is also what publishes. A release-candidate tag such as `v0.3.0rc1`
builds the wheel and, once the owner approves the run in GitHub, uploads it to
TestPyPI and makes a prerelease. A final tag such as `v0.3.0` does the same to
PyPI. Nothing is published without that approval. The design is
[ADR-028](https://github.com/twistingmercury/agentic_loop_runner/blob/develop/docs/design/02_architectural_decisions.md#adr-028-publish-releases-to-pypi-with-trusted-publishing-and-manual-approval).
```

- [ ] **Step 4: The runbook**

Write `docs/plans/2026-10-30-alr-pypi-release-runbook.md`, a checklist for the owner. These are the steps only the owner can do, in order:

1. PyPI: create an account at pypi.org, enable two-factor authentication.
2. PyPI: Account settings, "Publishing", add a pending publisher: PyPI project name `alr`, owner `twistingmercury`, repository `agentic_loop_runner`, workflow name `release.yaml`, environment name `pypi`.
3. TestPyPI: the same two steps at test.pypi.org (separate account).
4. GitHub: repository Settings, Environments, create `pypi`, add yourself under "Required reviewers". Optionally restrict deployment branches and tags to `v*`.
5. Merge the `feature/pypi-release` PR into develop.
6. Rehearsal 1: on develop, tag `v0.2.1rc1` (signed, annotated), push the tag, approve the `publish` job when it waits. Then on a clean machine or after `uv tool uninstall alr`: `uv tool install --index https://test.pypi.org/simple/ --index-strategy unsafe-best-match alr` and check `alr --version` prints `alr version: 0.2.1rc1 ...`. Check the GitHub release exists and is marked prerelease.
7. Rehearsal 2: tag `v0.2.1rc2`, push, approve. Then `uv tool upgrade alr` must move to `0.2.1rc2`.
8. If `alr` was refused at step 6: change `[project] name` to `alr-cli`, re-register both pending publishers under that name, and start again from step 6 with `rc3`.
9. The real release: bump commit (changelog `[0.2.1]` section, README version line), tag `v0.2.1`, push, approve. `uv tool install alr` from a clean state, and `alr --version`.
10. Push the wiki clone (the Install and Contributing edits from this plan are committed there, waiting).

- [ ] **Step 5: Lint the Markdown**

Run: `markdownlint README.md docs/plans/2026-10-30-alr-pypi-release-runbook.md` and, in the wiki clone, `markdownlint Install.md Contributing.md`.
Expected: nothing but the pre-existing MD013 line-length findings.

- [ ] **Step 6: Commit (repository and wiki separately; the wiki is not pushed)**

```bash
git add README.md docs/plans/2026-10-30-alr-pypi-release-runbook.md
git commit -s -m "Document the PyPI install path and the owner's release runbook"
cd ../agentic_loop_runner.wiki
git add Install.md Contributing.md
git commit -s -m "Install from PyPI; building from source moves to Contributing"
```

---

### Task 4: Full build and whole-branch review

**Files:** none new.

- [ ] **Step 1: Run the full build (main session only)**

Run: `./build/build.sh`
Expected: gates pass, 126 or more e2e tests pass, `dist/alr-0.2.0-py3-none-any.whl` is produced (the newest tag is still v0.2.0). This proves the raised `setuptools` floor builds in Docker.

- [ ] **Step 2: Confirm the wheel's metadata**

Run: `unzip -p dist/alr-*.whl 'alr-*.dist-info/METADATA' | grep -E "^(Name|Version|License-Expression|Author|Project-URL|Summary):"`
Expected: `Name: alr`, `Version: 0.2.0`, `License-Expression: MIT`, `Author: Jeremy K. Johnson`, three `Project-URL` lines, and the new `Summary`.

- [ ] **Step 3: Whole-branch review**

Use superpowers:requesting-code-review on `feature/pypi-release` against `develop`. The reviewer reads ADR-028 and checks the workflow against its permissions, trigger, and tag-routing rules, and the metadata against Task 1.

- [ ] **Step 4: Hand off**

Open the PR with superpowers:finishing-a-development-branch. The owner pushes. The runbook from Task 3 takes over after the merge.
