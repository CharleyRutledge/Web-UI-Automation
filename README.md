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

**Website requirements (Ireland / EU)** (`compliance:`, off by default): checks each page, before any
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
- broken links (internal, plus up to 60 external) and broken images
- sideways scrolling on a 375 px phone (WCAG 1.4.10)
- load time against `audit.load_budget_ms`
- HTTPS and security headers
- a WCAG scan, with a copyable fix for each issue

Each test lists every problem it finds, not just the first.

```bash
python -m ui_automation --config config/statespend.yaml -- site_audit tests/test_compliance.py
```

To audit another site, copy `config/statespend.yaml`, change `base_url`, and run it the same way. In GitHub,
go to **Actions → Site audit → Run workflow** and enter the config file. statespend.ie is audited weekly.

## Testing the framework itself

The framework has its own test suite in `selftests/` (separate from the UI tests in `tests/`). Every test
talks to real local servers over real sockets: a scenario website, SMTP servers (plain, STARTTLS, implicit
TLS, login), and local stand-ins for the Telegram and Anthropic APIs, used for the error paths
(401/429/500, timeouts, broken replies) the real services cannot produce on demand.

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -c selftests/pytest.ini selftests            # every push and weekly in CI (~2.5 min)
python -m pytest -c selftests/pytest.ini selftests -m load -s # weekly: load / performance numbers
python -m pytest -c selftests/pytest.ini selftests -m live    # weekly: real Claude + Telegram (needs secrets)
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
