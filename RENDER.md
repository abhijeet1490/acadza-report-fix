# Deploy through GitHub to Render

The server now accepts an explicit bind address, Render's PORT and public hostname,
and a configurable database path. Local defaults remain 127.0.0.1:8765.

1. Commit and push the hosting changes to the GitHub main branch.
2. In the Render dashboard, choose New, then Web Service.
3. Select Git Provider, authorize GitHub, and choose
   abhijeet1490/acadza-report-fix.
4. Set the following service settings.

| Setting | Value |
| --- | --- |
| Name | acadza-report-fix, or an available variant |
| Branch | main |
| Root Directory | Leave empty |
| Language / Runtime | Python 3 |
| Build Command | python -m unittest discover -s tests |
| Start Command | python -m acadza.server --host 0.0.0.0 --port $PORT |
| Health Check Path | /healthz |
| Instance count | 1 |

The committed .python-version selects the latest Render-supported Python 3.13
patch. Clear any conflicting PYTHON_VERSION environment override if one exists.
No pip installation, requirements file, frontend build or API key is needed for
the precomputed console.

5. Choose a storage arrangement before deploying.

**Free demo.** Select the Free instance type and keep the default database path.
Render discards local filesystem changes when it restarts, redeploys or spins
down. The console seeds its cases again, so recorded approvals, rejections and
audit entries can reset. Idle services spin down after 15 minutes, and cold
startup takes about a minute. The app remains a precomputed demo.

**Persistent review state.** Select a paid instance type, attach a persistent disk
mounted at /var/data, and set the environment variable below. Review the price
shown by Render before confirming the paid resources.

| Variable | Value |
| --- | --- |
| REVIEW_DB_PATH | /var/data/review.sqlite3 |

The application creates/imports the database at runtime after the disk is mounted.
Keep one service instance. Only files under /var/data are persisted.
A free Postgres database does not directly work with this SQLite implementation.

6. Render supplies RENDER_EXTERNAL_HOSTNAME automatically. Do not manually set it
   to the repository URL or include https:// in a public-host value.
7. Click Create Web Service and follow the build and runtime logs.
8. Wait for the service to report Live, then open the exact HTTPS URL Render shows.
9. Open that URL with /healthz appended. It should return {"status":"ok"}.
10. Check the queue has 39 questions and 44 reports. Open R001–R003, inspect the
    C-to-A diff, approve it, reload, and inspect the audit entry. Open R034 and
    confirm the action records a referral with the repair still pending.
11. With persistent storage, manually restart the service and verify the recorded
    decision and audit entry are still present. Reload the page after a restart
    to obtain the new request token.
12. Keep the service and its resources active for the assessment's seven-day window.
    Include the exact console URL in the submission and disclose precomputed
    analysis and any free-instance persistence limitations.

This is a public, shared demo workspace. Anyone with the URL can make demo review
decisions. Audit entries use the label Demo reviewer. Named faculty authentication
and production access controls are separate work.

If every case is already reviewed, select All cases or Reviewed to see them and
their audit entries. Scored results.json remains independent of demo decisions.

## Common failures

| Symptom | Check |
| --- | --- |
| No open port detected | Hosting commit is deployed and Start Command includes --host 0.0.0.0 --port $PORT |
| Host is not configured | RENDER_EXTERNAL_HOSTNAME matches the service hostname and the hosting commit is deployed |
| Missing requirements.txt | Replace the default Build Command with the command above |
| 404 at /healthz | The old commit is deployed |
| Read-only database or permission error | REVIEW_DB_PATH points into the mounted writable disk |
| Decisions reset | The database is on ephemeral storage rather than the persistent disk |
| Request-token error after restart | Reload the console before deciding |
| Invalid or incomplete batch error | Use the committed complete triage-run.json, and inspect build logs |

For later updates, commit and push to main. Git-provider-linked services can
auto-deploy pushes. If automatic deployment is disabled, choose Manual Deploy,
then Deploy latest commit.

Official references checked for this guide:

- [Web services and port binding](https://render.com/docs/web-services)
- [Default environment variables](https://render.com/docs/environment-variables)
- [Python version selection](https://render.com/docs/python-version)
- [Health checks](https://render.com/docs/health-checks)
- [Free-instance limitations](https://render.com/docs/free)
- [Persistent disks](https://render.com/docs/disks)
