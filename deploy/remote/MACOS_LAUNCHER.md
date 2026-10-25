# MediaCrawler Desktop Launcher

A one-time Mac installation creates three English-named applications:

| Application | Action |
| --- | --- |
| **MediaCrawler Start.app** | Start Workbench and Quick Tunnel in the background, update Worker upstream, and open the local Workbench |
| **MediaCrawler Stop.app** | Stop the two services owned by this launcher; preserve saved data |
| **MediaCrawler Status.app** | Show startup/running/error state, addresses, and the desktop log location |

Applications are generated locally under `~/Applications/MediaCrawler/`. You can drag MediaCrawler Start to the Dock. No terminal window needs to remain open after startup.

## One-time installation on the collection Mac

First complete the remote setup in `MACOS_WORKERS_DEV.md`: private `~/.config/mediacrawler/remote.env`, the Access-protected Worker, Node 22, uv, cloudflared, and a successful Wrangler OAuth login. The launcher retains Access authentication and does not start a collection task automatically.

Stop the two previously opened manual Workbench and Tunnel terminal processes using Control+C, after any collection task finishes. The launcher will not terminate an existing manual service or an external browser. Port conflicts produce an error rather than killing an unrelated process.

From the existing project folder:

```bash
git pull --ff-only
uv run --frozen python -m api.desktop install
```

Alternatively, double-click **Install MediaCrawler Launcher.command** in the project folder after pulling the new version. Both methods use the existing virtual environment and WebUI build; they do not replace your private environment file or databases. For a different private config path or ports:

```bash
uv run --frozen python -m api.desktop install \
  --env-file /absolute/private/path/remote.env \
  --local-port 8090 --remote-port 8081
```

The installer generates `.app` bundles using macOS `osacompile`, registers the current user's `com.mediacrawler.desktop` LaunchAgent and opens the application folder. It does not use root or start the services automatically. Existing files from an unknown installer or another project are preserved and installation stops. Reinstall only after using MediaCrawler Stop; earlier generated apps/settings are retained in the application folder's `.backups/` directory.

The earlier standalone local script is now **MediaCrawler Local.command**. Its local execution method is unchanged. Use the new Start/Stop applications for the managed remote setup instead of running both launch paths at once.

## Daily use

1. Double-click **MediaCrawler Start**.
2. Wait for startup. The first run may take longer while npm prepares the pinned Wrangler CLI. A successful start opens `http://127.0.0.1:8090` and displays a notification.
3. Other devices visit your existing workers.dev address and sign in through Access.
4. Double-click **MediaCrawler Stop** when finished. This also interrupts any collection task owned by the Workbench; already committed data remains saved.
5. Use **MediaCrawler Status** if startup fails or takes longer than expected.

Clicking Start repeatedly reuses the same registered service; it never uses `kickstart -k`, starts another Workbench, or creates another Tunnel for an already running launcher. Closing the browser does not stop the services. The Mac must remain awake, online and signed into the desktop user session. Logging out or shutting down stops access; this installation is one-click startup, not automatic login or automatic collection.

## Temporary address update

Each fresh start:

- Reads the private Access configuration and validates required tools/ports.
- Starts the same guarded dual-listener Workbench on loopback only.
- Checks actual local health and requires the protected listener to reject an anonymous request.
- Starts Quick Tunnel, waits for its generated address **and** registered connection.
- Probes the Tunnel's protected origin, expecting 401 without a JWT.
- Uses the Mac's existing Wrangler authorization to deploy the checked-out Worker relay with PUBLIC_ORIGIN and the new UPSTREAM_ORIGIN. The pinned CLI is `wrangler@4.152.0`; `--keep-vars` preserves other Worker variables. No Access policy is removed or changed.

The workers.dev address stays the same. You no longer need to manually copy each temporary Tunnel URL. The launcher updates the checked-out Worker source during deployment, so keep this Git branch current; do not treat edits made only in the Worker dashboard as the source of truth.

Ready means local Workbench health, connected Tunnel, protected origin and successful Worker update. It does **not** replace authenticated browser verification: the launcher never extracts browser cookies/JWTs to test your login. Normal Access sign-in is still required. If the Mac's Wrangler authorization expires, follow the error instruction and run in Terminal:

```bash
cd /path/to/MediaCrawler/deploy/remote/worker
npx wrangler@4.152.0 login
```

Then retry MediaCrawler Start. The launcher does not ask for a token or store credentials in its generated app or settings. Quick Tunnel still has its provider's temporary-service limits; this is not a fixed-Tunnel availability guarantee.

## Failures and ownership

If Workbench, Tunnel or Worker update fails, startup fails and only processes created by this supervisor are stopped. The UI does not announce ready after merely observing a PID or port. If either running child later exits, the other service is stopped and the status records an error; collection is never automatically restarted. cloudflared may reconnect transient network interruptions while its process remains alive.

A user-scoped LaunchAgent plus an OS lease prevents duplicate supervisor instances. Stop addresses that LaunchAgent, not a process name or a stale PID file. Workbench shuts down through its normal owned-task cleanup, leaving an already existing external CDP browser untouched. If forced termination leaves a collector behind, the existing execution lock prevents a new collection; inspect it locally instead of deleting lock files blindly.

Desktop state and rotated logs are under `<MC_RUNTIME_DIR or project/.runtime>/desktop/`. Logs rotate at 2 MB with two backups; messages pass through the existing credential redactor. Local launch settings contain executable paths, addresses and private config **file paths**, not the private config values. They are written with mode 600. macOS LaunchAgent stdout/stderr files are local; inspect them only if the supervisor cannot start.

Moving the project changes executable/module locations; stop the launcher and reinstall from its new folder rather than editing generated apps manually. For removal, stop the service, unload only `gui/<your uid>/com.mediacrawler.desktop`, then remove its LaunchAgent and the generated applications using Finder. Keep private environment, databases, media and backups.

## Verification scope

Cloud tests use temporary files, simulated app compilation, and harmless owned processes. They cover install preservation, English bundle metadata, process ownership, duplicate clicks, cancellation, port conflicts, Worker update failures, readiness/protection checks, and an actual paired Workbench startup/shutdown with only external Tunnel/Worker calls stubbed. They do not register launchctl on Linux, install macOS applications, perform real collection, or publish Cloudflare resources.

```bash
uv run --frozen pytest tests/test_desktop_launcher.py -q
```

Actual osacompile, LaunchAgent registration, background OAuth lookup, and opening the apps require final verification on your Mac.

### Cloud validation for this change

- 428 Python regression tests passed, including 17 launcher tests and actual owned Workbench process startup/shutdown with external Tunnel/Worker calls stubbed.
- All 5 Worker relay tests passed; Wrangler 4.152.0 confirms support for the preserved-variable deployment flag.
- English installer/local command scripts passed bash syntax checks; Git whitespace checks passed.
- Native AppleScript compilation, launchctl GUI registration and local Wrangler authorization still require the first installation run on the collection Mac. No production Tunnel, Cloudflare policy or collection task was started by cloud tests.
