# Web UI Automation

Playwright + Python + pytest suite following [Playwright testing practices](https://playwright.dev/docs/best-practices): semantic locators, web-first `expect()` assertions, Page Object Model, YAML config, pipeline video capture, and optional **Claude Sonnet 5.5** summaries with **email** and **Telegram** notifications.

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-FFDD00?style=flat-square&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/charleyrutledge)

## Quick start

```powershell

python -m pip install -r requirements.txt
python -m playwright install chromium
python -m ui_automation --open
```

## Open the report

| Path | Contents |
|------|----------|
| `reports/<timestamp>/report.html` | HTML report with embedded step screenshots |
| `reports/latest/report.html` | Copy of the last run |
| `reports/<timestamp>/summary.html` | Short phone-friendly report (sent by Telegram/email) with videos, screenshots and traces embedded |
| `reports/<timestamp>/videos/` | Recordings per browser test: `.webm`, plus `.mp4` when `ffmpeg` is installed (plays on iPhone) |
| `reports/<timestamp>/screenshots/` | A screenshot per test step |
| `reports/<timestamp>/failure-screenshots/` | Playwright's screenshot at the moment a test failed |
| `reports/<timestamp>/traces/` | Playwright trace `.zip` on failure (open at [trace.playwright.dev](https://trace.playwright.dev)) |
| `reports/<timestamp>/claude_summary.txt` | AI analysis (when enabled) |

```powershell
Invoke-Item .\reports\latest\report.html
```

## Accessibility and website requirements

**Accessibility** (`accessibility:` in `config/settings.yaml`, on by default): every page in `pages` is
scanned in the real browser with [axe-core](https://github.com/dequelabs/axe-core) 4.13 (vendored, checksum
verified). The default standard is **WCAG 2.1 AA**, the level EN 301 549 requires under the EU Web
Accessibility Directive (S.I. 358/2020) and the European Accessibility Act; `wcag22aa` adds the WCAG 2.2
criteria. Each issue is reported with its WCAG success criterion, the failing elements and, for each one, the corrected code (markup or CSS) with a Copy button, and the test fails
at `fail_on` severity (`none` = report only). Automated rules cannot prove conformance: the report lists what
still needs a person to check, following the W3C WCAG-EM method (scope, explore, sample, audit, report).
The report and dashboard themselves are tested against WCAG 2.2 AA (axe, keyboard, focus, 320 px reflow).

**Website requirements (Ireland / EU)** (`compliance:`, always on; `report_only: true` lists problems
without failing the run, for sites that aren't yours): checks each page, before any
consent is given, for a privacy notice link (GDPR Art. 13/14), no tracking cookies or tracker requests
before consent and a reject option (ePrivacy Regulations S.I. 336/2011, DPC guidance), an accessibility
statement link, company details (Companies Act 2014 s.151, S.I. 68/2003), contact details and terms.
Trackers are blocked during the check, so tests never send analytics. These checks find what is missing or
misbehaving; the wording of policies still needs review (and legal advice).

## Audit any website

`site_audit/` audits a whole site from its `base_url`. It finds the pages by following the site's own links,
home page first, up to `audit.max_pages`. Every page found is then checked for:

- loading, with a title and a main heading
- uncaught JavaScript errors
- network errors: every request the page makes (scripts, styles, images, fonts, API calls) that fails or
  gets HTTP 400 or above
- broken links (internal, plus up to 60 external) and broken images
- sideways scrolling at every screen size in `audit.screens` (by default a 320 px small phone for WCAG
  1.4.10, a phone, a tablet, a laptop and a desktop), with a screenshot at each size that breaks
- real phones (`audit.mobile_devices`, by default an iPhone 15 in Safari's engine and a Pixel 7 in Chrome):
  each page opened with the phone's screen, touch and mobile browser, as each role, and checked for a
  viewport tag, sideways scrolling, text under 12px, buttons too small to tap (axe's WCAG 2.5.8 rule) and
  errors; the phones take turns by day like the browsers
- the app's API: every API call the pages make (fetch / XHR) must answer without an error and within
  `audit.api_budget_ms`; each logged-in role's read requests are sent again with no login and as every
  other role, so data handed out without logging in, or to the wrong role, is reported. Only GET requests
  are replayed (nothing that changes data), and tokens never reach a report. Endpoints meant to be
  public go in `audit.public_api`
- load time against `audit.load_budget_ms`
- HTTPS and security headers
- a WCAG scan, with a copyable fix for each issue: axe-core's rules, plus what axe cannot check alone,
  done the way a person uses the page: Tab through it (focus never stuck, always visible, nothing
  mouse-only), larger text spacing (no text cut off), alt text that says nothing, videos without
  captions, automatic refresh, endless animation with no pause, and skipped heading levels. The report
  lists what is checked automatically and what still needs a person
- Irish/EU website requirements on the home page

Pages built in the browser (React, Vue, ...) are measured only once they show real content: the audit waits
until the page has visible text with no "Loading..." or spinner, and has stopped changing (up to
`audit.content_wait_ms`, 15 s by default). A page that never gets there is reported as still loading, not
as a page with no heading.

Each page is opened once and all of this is measured on that visit, so a 25-page audit stays quick.
Each check lists every problem it finds, not just the first.

**Several browsers, for any app.** By default every page is checked in Chrome's engine, Firefox and WebKit
(the engine Safari uses), set with `browsers: [chromium, firefox, webkit]` (or `browser: chromium` for
one only); a missing browser is installed automatically before the run.
Checks that do not depend on the browser (links, HTTPS, legal pages) run once. The report's buttons
filter by browser and role. Each run uses one of them, taking turns by day (`browser_rotation: daily`,
the default), so runs stay quick; `--all-browsers` runs every one (e.g. before a release), and
`browser_rotation: off` always runs all of them.

**Nothing is skipped.** A check that does not apply to the run (HTTPS on a local app, a site-wide check
again for each role, permission checks for a role with no restricted pages listed) is not run at all, and
the end of the run lists each one with the reason, so you can see exactly what was not covered.

```bash
python -m ui_automation --config config/site-audit.yaml --url https://example.ie -- site_audit
```

`--url` works with any settings file and names the report after that site. In GitHub, go to
**Actions → Site audit → Run workflow** and type the website's address. Problems found on the site show
as a warning on a green run (the report is the result); the run fails only when the audit itself
could not run.

**Nothing runs on its own.** No suite is scheduled, and none that visits a website or sends a report
runs on push. The site audit, the practice sites, the playwright.dev browser suite and the load and live
checks only run when you start them in **Actions** (the site audit only with the URL you type). The
self-tests and security scan still run on every push and pull request; they use local test servers and
send nothing.

## Test behind a login, with roles

The site audit can log in and test as several kinds of user. It runs once logged out and once per role,
and the report labels every check with its role (e.g. `admin · chromium`).

```yaml
auth:
  login_url: /login
  roles:
    - name: admin
      username: "${APP_ADMIN_USER}"
      password: "${APP_ADMIN_PASSWORD}"
      start: /dashboard
    - name: viewer
      username: "${APP_VIEWER_USER}"
      password: "${APP_VIEWER_PASSWORD}"
      must_not_access: [/admin]   # checked: this role must be refused these pages
      pages: [/settings]          # optional: pages no link leads to, checked as well
      # area: /app                # optional: keep this role's crawl to pages under /app
```

- **Each role checks its own pages.** Pages the logged-out visitor already checked (and could really open)
  are not opened again by the roles, so each role's page budget (`audit.max_pages`) goes to the signed-in
  part of the app. The report lists every page each role checked.

- **Credentials** come from environment variables or the git-ignored `.env`, never from the settings file.
  A missing one is named in the error.
- **The login form** is found automatically (email or username box, password box, log-in button),
  including two-step logins where the password comes on a second screen. Set `username_field`,
  `password_field`, `submit` or `logged_in_check` if your form needs them.
- **No recording of the login.** It runs in a browser session with no video, trace or screenshot. Only the
  session it creates is used by the audit, so a password cannot end up in a report.
- **Safe crawling.** Log-out, delete and similar links are never followed or requested, so roles stay
  logged in and no data is changed.
- **Site-wide checks** (HTTPS, website requirements) run once, not again for every role.

## Test an app on your own computer (localhost)

Apps on your computer or local network (`localhost`, `127.0.0.1`, `192.168.x.x`, `*.local`) are tested
from **your computer**: GitHub's machines cannot reach them.

```bash
# The app is already running:
python -m ui_automation --config config/meridian-data.yaml -- site_audit
# Another port:
python -m ui_automation --config config/meridian-data.yaml --url http://localhost:5173 -- site_audit
# Let the tests start the app, wait until it answers, and stop it afterwards:
python -m ui_automation --config config/meridian-data.yaml --start "npm run dev" --start-in ../meridian-data -- site_audit
```

- Apps in **Docker**: a start command that runs in the background (`docker compose up -d --wait`) is
  fine. Give it a stop command (`--stop "docker compose down"`) to shut the app down after the tests.
- Save the commands in the settings file instead of typing them each time:
  ```yaml
  app:
    start: "docker compose up -d --wait"
    stop: "docker compose down"
    start_in: "C:/Users/me/meridian-data"
  ```
  If the app is already running, it is tested as it is and left running. `--no-start` never starts it.
- If nothing is answering at the address, the app says so and stops. It runs no tests and creates no
  empty report.
- With `--start`, the app's own output is saved as `app-server.log` in the run folder. If the app fails
  to start, or doesn't answer within `--start-timeout` (120 s), the run stops and the app is shut down.
- Self-signed HTTPS certificates are accepted for local addresses only. The HTTPS and security-header
  checks are left for the deployed site (`audit.security_checks: on` runs them locally too).
- For Telegram reports from your computer, copy `.env.example` to `.env` and fill in your keys. `.env`
  is git-ignored and never leaves your computer; GitHub runs use the repository secrets.

## Package updates

`.github/dependabot.yml` keeps every package up to date: each Monday GitHub checks the Python packages and
the GitHub Actions, and opens **one** pull request with all available updates. The self-tests run on it;
it is merged by hand like any other change. On your computer, `git pull` and then
`python -m pip install -U -r requirements.txt` brings the new versions in.

## Testing the framework itself

The framework has its own test suite in `selftests/` (separate from the UI tests in `tests/`). Every test
talks to real local servers over real sockets: a scenario website, SMTP servers (plain, STARTTLS, implicit
TLS, login), and local stand-ins for the Telegram and Anthropic APIs, used for the error paths
(401/429/500, timeouts, broken replies) the real services cannot produce on demand.

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -c selftests/pytest.ini selftests -n auto    # every push and pull request in CI (~3 min on 4 cores)
python -m pytest -c selftests/pytest.ini selftests -m load -s # by hand (Actions -> Extended tests): load / performance numbers
python -m pytest -c selftests/pytest.ini selftests -m live    # by hand (Actions -> Extended tests): real Claude + Telegram
```

| Area | What is covered |
|------|-----------------|
| Config | every setting: valid, boundary, wrong type, missing, `${ENV}` values, broken YAML |
| Browser | pass, missing element, slow page, HTTP 500, redirect loop, offline, DNS failure, bad TLS certificate, throttled network, fixture error, skip, xfail, and the artifacts each leaves |
| CLI | exit codes, test selection, no tests, bad/missing config, Ctrl-C, same-second runs, unwritable reports folder, no `ffmpeg`, `--open` without a browser |
| Email | plain, STARTTLS, implicit TLS, untrusted certificates, login ok/wrong/missing, rejected recipients, server down, slow server, large report |
| Telegram | document + caption, chat discovery, 400/401/429/500/502, broken JSON, timeout, connection refused, caption limit, token never leaked |
| Claude | request contents, no call on green runs, 400/401/404, 429 retry, 500/529 give-up, refusal, timeout, prompt size cap, failures never break the run |
| Security | report escaping (test names, messages, AI text, media paths), dashboard XSS, path traversal and symlink escape, CSRF, DNS rebinding, loopback-only binding, secret scan of all output and artifacts, `pip-audit`, `bandit` |
| Accessibility | our report and dashboard meet WCAG 2.2 AA (axe, keyboard, focus, reflow, text alternatives); scanning of accessible and broken pages; standards and thresholds |
| Website requirements | compliant vs non-compliant shop page: tracking cookies and tracker requests before consent, accept-only banners, missing or broken links, company details |
| Load | dashboard with 2,000 runs under 16 concurrent clients, 5 MB report downloads, a 500-test run, concurrent CLI runs, report size budget |

The `CLI` also honours `WEB_UI_REPORTS_DIR` (where run folders go) and `TELEGRAM_API_BASE` (Bot API address).

## Dashboard (optional local UI)

Browse past runs and trigger new ones from a small local web page instead of the CLI:

```powershell
python -m pip install -r requirements-ui.txt
python -m ui_automation --ui
```

Opens at `http://127.0.0.1:8501` by default (`--ui-port` to change it). Lists every run under
`reports/` with pass/fail/skip/error counts, a **Run tests** button, and a link to each run's
HTML report.

## Playwright best practices in this repo

- **Locators** in `pages/locators/` — `get_by_role`, `get_by_label`, `get_by_test_id` (not CSS/XPath-only).
- **Assertions** — `expect(locator).to_be_visible()` etc. (auto-retrying).
- **Navigation** — relative URLs with `base_url` (`page.goto("/path")`).
- **No arbitrary sleeps** — timeouts from `timeout_ms` and Playwright auto-wait.
- **`strict_selectors: true`** — ambiguous locators fail fast.
- **Isolation** — fresh browser context per test (`pytest-playwright`).
- **Artifacts** — video, trace, and step screenshots for debugging.

## Video recording (local + CI)

`config/settings.yaml`:

```yaml
artifacts:
  video: on   # on | retain-on-failure | off
```

The CLI passes `--video` and stores output under `reports/<timestamp>/playwright-output/`, then copies `.webm` files to `reports/<timestamp>/videos/`.

In **GitHub Actions** (`CI=true`), video is always forced to `on`. The workflow installs `ffmpeg` (for the MP4 copies) and uploads four artifacts: `test-report` (summary + full HTML report), `test-videos`, `test-screenshots` and `playwright-traces`.

## Claude Sonnet 5.5 (AI)

Uses the Anthropic API with model **`claude-sonnet-5-5`**.

1. Set `ANTHROPIC_API_KEY`.
2. In `config/settings.yaml`:

```yaml
ai:
  enabled: true
  model: claude-sonnet-5-5
  max_tokens: 2048
```

After each run, a short failure/success analysis is written to `claude_summary.txt` and included in email/Telegram when those channels are enabled.

## Email notifications

```yaml
notifications:
  email:
    enabled: true
    smtp_host: smtp.example.com
    smtp_port: 587
    smtp_user: "${EMAIL_SMTP_USER}"
    smtp_password: "${EMAIL_SMTP_PASSWORD}"
    from_addr: automation@example.com
    to_addrs:
      - qa@example.com
```

Environment variables replace `${VAR}` placeholders in YAML.

## Telegram notifications

```yaml
notifications:
  telegram:
    enabled: true
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"
```

Create a bot via [@BotFather](https://t.me/BotFather), add the bot to a chat, and use your chat id.

## Project layout

```
Automation/
  config/settings.yaml
  pages/
    locators/              # Centralized selectors
    base_page.py           # Semantic helpers + step()
    playwright_landing_page.py
  tests/
  conftest.py
  ui_automation/
    cli.py
    config.py
    reporting/             # Claude, email, Telegram, video copy
  .github/workflows/ui-tests.yml
```

## Running tests

```powershell
python -m ui_automation
python -m ui_automation -- -m smoke
python -m ui_automation -- --headed --slowmo=300
```

## CI secrets (GitHub)

| Secret | Purpose |
|--------|---------|
| `ANTHROPIC_API_KEY` | Claude summaries |
| `TELEGRAM_BOT_TOKEN` | Telegram bot |
| `TELEGRAM_CHAT_ID` | Telegram destination |
| `EMAIL_SMTP_USER` / `EMAIL_SMTP_PASSWORD` | SMTP auth |

Enable notification blocks in `settings.yaml` (or use `settings.example.yaml` as a template).

## Adding tests

1. Add locators under `pages/locators/`.
2. Extend `BasePage` with role/label helpers.
3. Call `self.step("...")` for HTML report screenshots.
4. Add tests under `tests/`.

## Support this project

If this suite saves you time, you can support ongoing work with a small donation:

**[Buy Me a Coffee](https://buymeacoffee.com/charleyrutledge)**

GitHub also shows a **Sponsor** link on the repo (from [`.github/FUNDING.yml`](.github/FUNDING.yml)). If your Buy Me a Coffee username is not `charleyrutledge`, update that file and the links above to match your profile URL.
