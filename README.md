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
