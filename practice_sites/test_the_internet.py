"""https://the-internet.herokuapp.com — forms, dialogs, dynamic content, uploads, status codes."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import expect

from pages.base_page import BasePage

URL = "https://the-internet.herokuapp.com"


def test_login_succeeds_with_valid_credentials(site: BasePage) -> None:
    site.goto_path(f"{URL}/login")
    site.fill_label("Username", "tomsmith", step_label="Enter username")
    site.fill_label("Password", "SuperSecretPassword!", step_label="Enter password")
    site.click_role("button", name="Login", step_label="Click Login")
    site.step("Assert logged in")
    expect(site.page.locator("#flash")).to_contain_text("You logged into a secure area!")
    site.click_role("link", name="Logout", step_label="Log out")
    expect(site.page.locator("#flash")).to_contain_text("You logged out of the secure area!")


def test_login_rejects_wrong_password(site: BasePage) -> None:
    site.goto_path(f"{URL}/login")
    site.fill_label("Username", "tomsmith")
    site.fill_label("Password", "wrong", step_label="Enter a wrong password")
    site.click_role("button", name="Login")
    site.step("Assert error shown")
    expect(site.page.locator("#flash")).to_contain_text("Your password is invalid!")
    expect(site.page).to_have_url(f"{URL}/login")


def test_checkboxes(site: BasePage) -> None:
    site.goto_path(f"{URL}/checkboxes")
    boxes = site.page.locator("#checkboxes input[type=checkbox]")
    expect(boxes).to_have_count(2)
    boxes.nth(0).check()
    boxes.nth(1).uncheck()
    site.step("Toggled both checkboxes")
    expect(boxes.nth(0)).to_be_checked()
    expect(boxes.nth(1)).not_to_be_checked()


def test_dropdown(site: BasePage) -> None:
    site.goto_path(f"{URL}/dropdown")
    site.page.locator("#dropdown").select_option(label="Option 2")
    site.step("Selected Option 2")
    expect(site.page.locator("#dropdown")).to_have_value("2")


def test_add_and_remove_elements(site: BasePage) -> None:
    site.goto_path(f"{URL}/add_remove_elements/")
    for _ in range(3):
        site.click_role("button", name="Add Element")
    added = site.page.locator("#elements button")
    expect(added).to_have_count(3)
    added.first.click()
    site.step("Added 3, removed 1")
    expect(added).to_have_count(2)


def test_dynamic_loading_waits_for_hidden_element(site: BasePage) -> None:
    site.goto_path(f"{URL}/dynamic_loading/1")
    site.click_role("button", name="Start", step_label="Click Start")
    expect(site.page.locator("#finish")).to_have_text("Hello World!", timeout=20_000)
    site.step("Hello World! appeared")


def test_javascript_alert_confirm_and_prompt(site: BasePage) -> None:
    page = site.page
    site.goto_path(f"{URL}/javascript_alerts")
    page.once("dialog", lambda d: d.accept())
    site.click_role("button", name="Click for JS Alert")
    expect(page.locator("#result")).to_have_text("You successfully clicked an alert")
    page.once("dialog", lambda d: d.dismiss())
    site.click_role("button", name="Click for JS Confirm")
    expect(page.locator("#result")).to_have_text("You clicked: Cancel")
    page.once("dialog", lambda d: d.accept("playwright"))
    site.click_role("button", name="Click for JS Prompt")
    site.step("Answered all three dialogs")
    expect(page.locator("#result")).to_have_text("You entered: playwright")


def test_file_upload(site: BasePage, tmp_path: Path) -> None:
    upload = tmp_path / "hello-upload.txt"
    upload.write_text("uploaded by the UI automation framework\n")
    site.goto_path(f"{URL}/upload")
    site.page.locator("#file-upload").set_input_files(upload)
    site.page.locator("#file-submit").click()
    site.step("File uploaded")
    expect(site.page.locator("#uploaded-files")).to_have_text("hello-upload.txt")


def test_status_codes_are_real(site: BasePage) -> None:
    for code in (200, 301, 404, 500):
        response = site.page.goto(f"{URL}/status_codes/{code}")
        assert response is not None
        # A 301 with a Location header is followed by the browser; without one the 301 itself is the answer.
        expected = (200, 301) if code == 301 else (code,)
        assert response.status in expected, f"/status_codes/{code} answered {response.status}"
    site.step("Checked 200, 301, 404 and 500")


def test_broken_images_are_detected(site: BasePage) -> None:
    """The page deliberately has broken images: prove the framework can find them."""
    site.goto_path(f"{URL}/broken_images")
    site.page.wait_for_load_state("load")
    broken = site.page.evaluate(
        "() => [...document.querySelectorAll('.example img')].filter(i => i.naturalWidth === 0).length"
    )
    site.step(f"{broken} broken image(s) found")
    assert broken >= 1, "the demo page is supposed to contain broken images"
