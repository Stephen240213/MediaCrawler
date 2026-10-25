# MediaCrawler development

## Scope and environments

- Use Codex Cloud for code changes, dependency installation, builds, and tests.
- Real collection, interactive Chrome login, and existing user data stay on the local computer. Cloud tasks must not initiate real collection or publishing without a new explicit request.
- Do not upload or commit `.runtime/`, `data/`, browser profiles, cookies, `.env`, or local credentials. A Git checkout does not include these files.
- Keep upstream history and local workbench functionality. Merge upstream updates on a separate branch and validate before integrating; never reset the fork to upstream or force-push shared branches.

## Setup and validation

- Python version: `.python-version`; install dependencies using `uv sync --frozen` and the committed `uv.lock`.
- Use Node.js 22 and the npm lockfile under `webui/`.
- Install cloud dependencies with `bash scripts/cloud-setup.sh`. This installs headless Chromium for local fixture tests, not interactive account login.
- Run `bash scripts/cloud-check.sh` for the workbench regression suite and frontend production build. Browser fixture tests intercept their requests locally; do not replace them with real account tests.
- Tests use Playwright's installed Chromium by default. To use a local Chrome binary, set `MEDIACRAWLER_TEST_BROWSER` to its absolute path.
- Build output in `api/webui/` is generated and ignored. Do not commit dependency directories or runtime files.
- For API development previews only: `uv run --frozen uvicorn api.main:app --host 127.0.0.1 --port 8080`. Do not expose the unauthenticated API publicly.
- Report the actual environment and checks used. Local checks do not establish that cloud provisioning or real collection succeeded.

## Delivery

- After completing project changes and the required checks, commit the intended changes and push the current branch to `origin`. Report the commit and pushed branch.
- Never include private configuration, credentials, model weights, runtime files, browser profiles, or user data in the commit. Do not force-push shared branches.
