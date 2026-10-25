# Apple pickup inventory monitor (Resend)

Existing Python Apple stock monitor, configured for **iPhone Duo, Black, 512GB, unlocked**, exact SKU **MK1H4LL/A**, ZIP **92602**. The product label is supplied by the user; the SKU controls the inventory lookup. All returned stores are checked (12 in the verified response). Alerts put Irvine Spectrum Center, South Coast Plaza, Fashion Island, and Mission Viejo first.

Repository: https://github.com/yuxi-fan/monitor2026Oct. Code changes go directly on **master**. `monitor-state` is a durable data branch for deduplication, not a feature/development branch. Do not merge it into master or reset it during normal operation.

## Windows setup

Install Python 3.11+ and Git for Windows if needed. In PowerShell:

```powershell
Set-Location 'C:\Users\yuxif\Downloads\apple_stock_monitor\apple_stock_monitor'
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe monitor.py --part MK1H4LL/A --zip 92602 --once --dry-run
```

Use your Python executable instead of `py -3` if necessary. No PowerShell activation is required. Dry-run logs inventory but never sends email or writes stock state. Local checks use `state.json` by default; cloud checks use the durable state branch. A failed one-shot check exits nonzero.

The working Apple API remains GET `https://www.apple.com/shop/retail/pickup-message` with `pl=true`, `parts.0=MK1H4LL/A`, and `location=92602`. The old endpoint is not used.

## Configure Resend

1. Create or sign in to your account at https://resend.com.
2. For ongoing use, add your sending domain in **Domains**, add the DNS records Resend supplies at your DNS provider, then wait for verification. Use a sender on that verified domain, for example `Apple Monitor <alerts@your-domain.com>`. The example is not a credential or a configured sender.
3. For an initial test without a verified domain, Resend allows `onboarding@resend.dev` as the sender **only when sending to the email address associated with your Resend account**. Set `ALERT_EMAIL` to that address. Other recipients require your own verified domain. You cannot use an arbitrary Gmail address as a verified-domain sender.
4. Open **API Keys**, create a key with **Sending access**, scoped to your sending domain when available, and copy it directly to the GitHub secret below. Do not paste the key into source, commits, logs, issues, or chat.
5. Review the account's current sending quota and any account/domain restrictions in Resend. Rate limits and rejected sends cause a failed check without advancing stock state.

References: [Send Email API](https://resend.com/docs/api-reference/emails/send-email), [resend.dev sender restriction](https://resend.com/docs/knowledge-base/403-error-resend-dev-domain), [API errors](https://resend.com/docs/api-reference/errors).

Gmail SMTP is no longer used. Google app passwords and 2-Step Verification are not needed for this monitor. You may remove old `GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD` GitHub secrets and revoke an app password created solely for the old monitor.

## Configure GitHub Actions

Open https://github.com/yuxi-fan/monitor2026Oct/settings/secrets/actions.

Under **Repository secrets > New repository secret**, configure:

| Secret | Value |
| --- | --- |
| `RESEND_API_KEY` | Your Resend sending API key |
| `ALERT_EMAIL` | One recipient email address |
| `STATE_TOKEN` | Fine-grained GitHub token for durable stock-state writes |

Open **Settings > Secrets and variables > Actions > Variables > New repository variable** and create:

| Variable | Value |
| --- | --- |
| `RESEND_FROM_EMAIL` | An allowed sender, e.g. `Apple Monitor <alerts@your-domain.com>` or the restricted test sender `onboarding@resend.dev` |

You may alternatively store `RESEND_FROM_EMAIL` as a repository secret of the same name; the workflow uses the variable first and falls back to the secret. In local runs it is an environment variable. The sender is required; there is no hardcoded fallback.

### Durable stock state

The workflow's built-in `GITHUB_TOKEN` stays at **contents: read**. Updating state therefore requires the separate `STATE_TOKEN`:

1. GitHub profile **Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token**.
2. Select resource owner `yuxi-fan`, **Only select repositories**, and `monitor2026Oct`.
3. Grant **Repository permissions > Contents: Read and write** (Metadata read is automatic). No Workflows/Actions/admin access is needed for this state token.
4. Set an expiration you will maintain, generate the token, and save it as repository secret `STATE_TOKEN`. Renew before expiration.
5. On the Code tab verify **monitor-state** exists. If missing, use the branch selector on **master**, type `monitor-state`, and create it from master. Leave master as the default branch. Rules for this data branch must allow the token owner to write it.

The fine-grained token is repository-scoped, not branch-scoped. This code writes only `stock-state.json` on `monitor-state`; protect the token accordingly. State is read on every cloud run and committed only when known stock status changes. The stock-state file does not contain email credentials. A corrupt file, missing branch, or invalid token fails closed rather than starting with empty history.

### Enable the workflow

1. On **Settings > General**, verify the repository is private if desired, and the default branch is **master**.
2. On **Settings > Actions > General**, allow GitHub Actions and the official checkout/setup-python actions. Default workflow permissions can remain read-only.
3. Open **Actions**, enable workflows if prompted, and select **Apple pickup monitor**.

## Test email delivery immediately in GitHub Actions

1. Select **Run workflow**, choose **master**.
2. Set **test_email = true** and **dry_run = true** for a harmless rehearsal. Run it; no HTTP email request, Apple lookup, or stock-state read/write occurs.
3. Run again with **test_email = true** and **dry_run = false**.
4. Open the run. Tests should pass and logs should say **Resend accepted email**. The test sends a clearly labeled `Apple stock monitor: Resend delivery test` message to `ALERT_EMAIL`. It never changes inventory or alert history, and does not require `STATE_TOKEN`.
5. Open Resend's **Emails** dashboard, inspect the delivery status, and check your recipient inbox and spam folder. API acceptance alone does not prove inbox delivery. If testing from `onboarding@resend.dev`, the recipient must be your Resend account email.

The test intentionally sends even when stock is unavailable. Regular inventory alerts still send only for newly available stores. Each explicit non-dry email test sends a new message.

## Test inventory and enable unattended monitoring

1. Run on **master** with **test_email = false**, **dry_run = true**. Confirm the inventory step logs all returned stores (12 expected from the supplied verified response) and no message/state write occurs. This requires state-read access but does not validate state-write access.
2. Run with **test_email = false**, **dry_run = false**. A store first observed available sends an alert; unavailable stock sends nothing. Confirm `stock-state.json` appears on the data branch after the first successful normal run.
3. Repeat after a reasonable interval. Unchanged available stock should not send another alert. An observed unavailable-to-available change should send again. Test-email runs never affect this history.
4. After several expected intervals, inspect Actions for successful runs with event **schedule**. Unchanged stock causes no commit, so check run history, not just state commit timestamps.

The workflow requests a run every ten minutes (UTC minutes 7, 17, 27, 37, 47, 57). **GitHub schedules can be delayed or dropped under load and are not guaranteed to execute exactly every ten minutes.** Scheduled runs use the default branch, which must contain the workflow. Once configured, your Windows computer can be off.

Concurrency serializes manual and scheduled jobs without canceling an active run; an older pending run may be replaced. A job has a five-minute timeout, Python 3.11, pip caching, and read-only default permissions. Tests run before monitoring or sending a test email. Review Actions billing/runner quota for this private repository: about 144 scheduled jobs are requested per day. Enable GitHub workflow-failure notifications and periodically check run history.

## Local email test and CLI

Set `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, and `ALERT_EMAIL` in your process environment through your preferred secure method. Then:

```powershell
# Sends a delivery test without Apple API calls or stock-state access:
.\.venv\Scripts\python.exe monitor.py --test-email
# Sends nothing, including when --test-email is specified:
.\.venv\Scripts\python.exe monitor.py --test-email --dry-run
# One real inventory check, with local state:
.\.venv\Scripts\python.exe monitor.py --part MK1H4LL/A --zip 92602 --once
# Local periodic monitor (computer must remain awake):
.\.venv\Scripts\python.exe monitor.py --part MK1H4LL/A --zip 92602 --interval 600
```

`--part`, `--zip`, `--once`, `--dry-run`, and `--interval` remain supported. Minimum interval is 300 seconds. `--state-file PATH` changes local state location. `--state-backend github` uses `GITHUB_REPOSITORY= yuxi-fan/monitor2026Oct` (without the space after `=`) and `STATE_TOKEN` environment variables. Local and cloud state are separate by default, so each may produce its own first-observation alert. Do not run a second writer against the cloud state outside the serialized workflow.

## Reliability and failure behavior

- Only literal `available` and `unavailable` values from the exact SKU's `pickupDisplay` update state. Unknown/missing SKU/status and absent stores preserve the last known state. No valid expected-SKU statuses means a failed run without state changes.
- Every returned store is checked. The response count is logged rather than hardcoded to 12. State is scoped by SKU and ZIP.
- First-observed available stock and unavailable-to-available changes alert. Repeated available observations, including available-to-unknown-to-available, do not alert.
- The Apple Session retains connect/read timeouts of 10/30 seconds, three backoff retries for temporary network/server errors, and immediate stop on HTTP 429 or 541. No protection bypass or older API fallback is used.
- Resend uses HTTPS POST `/emails`, bearer API-key authentication, configured sender/recipient, and 10/30-second timeouts. Transient network errors and 500/502/503/504 responses get at most two retries, using the same per-send idempotency key and payload. 429 and other errors stop without aggressive retries. Redirects are rejected.
- Only a successful JSON response with an email ID confirms acceptance. Error logs contain fixed messages and HTTP status codes; raw response bodies, authorization headers, keys, sender, and recipient are never logged. Inspect the Resend dashboard for details.
- Notification failure leaves previous stock state unchanged, allowing the next normal check to retry. GitHub commits use SHA comparison to reject conflicting writes. Invalid JSON, bad store identity, API errors, or corrupt state fail closed.
- Resend acceptance and the GitHub commit are separate transactions. If the email is accepted but the state commit fails, a later run may repeat the alert. Per-send idempotency protects in-process retries, not separate scheduled runs; exactly-once delivery across both services is not guaranteed. This preserves the existing durable stock-change deduplication behavior without changing the saved-state format.
- This monitor never purchases/reserves anything. Apple's API is undocumented, and stock can disappear before checkout. Code/tests passing does not mean account configuration, actual cloud API access, or inbox delivery has been verified.

## Troubleshooting

- **Resend 401/403**: check API key, sending permissions, sender domain verification, and the resend.dev recipient restriction.
- **Resend 422**: inspect sender/recipient formatting and the dashboard.
- **429 or 5xx/network failures**: check quota/service status; failed notifications do not advance stock history.
- **Test passes but no inbox mail**: inspect Resend Emails for delivered/bounced/rejected/suppressed status, then check spam. Acceptance is not an inbox guarantee.
- **Inventory check failed**: follow the last logged stage (load state, Apple lookup, send, save); verify state branch/token and Apple access. Failure details omit sensitive payloads.
- **No scheduled runs**: confirm master is the default branch, workflow is enabled, runner quota remains, and allow for GitHub scheduling delays.
- **Repeated Apple 429/541**: pause the workflow if persistent; do not increase frequency or bypass blocking.
- **Old local state**: the old original pre-cloud monitor used a different JSON format. Rename it and ignore the renamed file before switching; never silently reset valid cloud history.

## Repository commands

For a new local checkout of the now-populated repository:

```powershell
git clone https://github.com/yuxi-fan/monitor2026Oct.git
Set-Location monitor2026Oct
git switch master
```

For future updates in the existing configured folder, inspect `git status`, stage only intended source files, commit, and `git push origin master`. Do not commit `.venv`, `.env`, credentials, or local state. No feature branch is required. Do not force-push over remote changes.

References: [GitHub scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [GitHub contents API](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents).
