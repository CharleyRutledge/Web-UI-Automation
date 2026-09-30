"""UI Testing Playground, DemoQA, Automation Exercise, Demoblaze, ParaBank, testautomationpractice."""

from __future__ import annotations

import re

from playwright.sync_api import expect

from pages.base_page import BasePage

UITP = "http://uitestingplayground.com"


# ---------------------------------------------------------------- UI Testing Playground


def test_uitp_dynamic_id_button(site: BasePage) -> None:
    site.goto_path(f"{UITP}/dynamicid")
    site.click_role("button", name="Button with Dynamic ID", step_label="Click the button (its id changes every load)")


def test_uitp_text_input_renames_button(site: BasePage) -> None:
    site.goto_path(f"{UITP}/textinput")
    site.page.locator("#newButtonName").fill("Renamed by Playwright")
    site.page.locator("#updatingButton").click()
    site.step("Button renamed")
    expect(site.page.locator("#updatingButton")).to_have_text("Renamed by Playwright")


def test_uitp_ajax_data_arrives(site: BasePage) -> None:
    site.goto_path(f"{UITP}/ajax")
    site.page.locator("#ajaxButton").click()
    expect(site.page.locator(".bg-success")).to_have_text("Data loaded with AJAX get request.", timeout=25_000)
    site.step("AJAX data arrived")


def test_uitp_sample_app_login(site: BasePage) -> None:
    page = site.page
    site.goto_path(f"{UITP}/sampleapp")
    page.locator("input[name=UserName]").fill("tester")
    page.locator("input[name=Password]").fill("pwd")
    page.locator("#login").click()
    site.step("Logged in")
    expect(page.locator("#loginstatus")).to_have_text("Welcome, tester!")
    page.locator("#login").click()
    expect(page.locator("#loginstatus")).to_have_text("User logged out.")


def test_uitp_sample_app_wrong_password(site: BasePage) -> None:
    page = site.page
    site.goto_path(f"{UITP}/sampleapp")
    page.locator("input[name=UserName]").fill("tester")
    page.locator("input[name=Password]").fill("wrong")
    page.locator("#login").click()
    expect(page.locator("#loginstatus")).to_have_text("Invalid username/password")


def test_uitp_progress_bar_stop_at_75(site: BasePage) -> None:
    page = site.page
    site.goto_path(f"{UITP}/progressbar")
    page.locator("#startButton").click()
    bar = page.locator("#progressBar")
    expect(bar).to_have_attribute("aria-valuenow", re.compile(r"^(7[5-9]|[89]\d|100)$"), timeout=30_000)
    page.locator("#stopButton").click()
    value = int(bar.get_attribute("aria-valuenow") or 0)
    site.step(f"Stopped at {value}%")
    assert 75 <= value <= 85, f"stopped at {value}%"


# ---------------------------------------------------------------- DemoQA


def test_demoqa_text_box_form(site: BasePage) -> None:
    page = site.page
    site.goto_path("https://demoqa.com/text-box")
    page.locator("#userName").fill("Test User")
    page.locator("#userEmail").fill("test.user@example.com")
    page.locator("#currentAddress").fill("1 Main Street, Dublin")
    page.locator("#submit").click()
    site.step("Form submitted")
    expect(page.locator("#output #name")).to_contain_text("Test User")
    expect(page.locator("#output #email")).to_contain_text("test.user@example.com")


def test_demoqa_rejects_invalid_email(site: BasePage) -> None:
    page = site.page
    site.goto_path("https://demoqa.com/text-box")
    page.locator("#userEmail").fill("not-an-email")
    page.locator("#submit").click()
    site.step("Submitted an invalid email")
    expect(page.locator("#userEmail")).to_have_class(re.compile("field-error"))
    expect(page.locator("#output #email")).to_have_count(0)


# ---------------------------------------------------------------- Automation Exercise


def test_automationexercise_product_search(site: BasePage) -> None:
    page = site.page
    site.goto_path("https://automationexercise.com/products")
    page.locator("#search_product").fill("top")
    page.locator("#submit_search").click()
    site.step("Searched for 'top'")
    expect(page.get_by_role("heading", name=re.compile("Searched Products", re.I))).to_be_visible()
    expect(page.locator(".productinfo").first).to_be_visible()


def test_automationexercise_login_rejects_unknown_user(site: BasePage) -> None:
    page = site.page
    site.goto_path("https://automationexercise.com/login")
    page.locator("[data-qa=login-email]").fill("nobody.here.12345@example.com")
    page.locator("[data-qa=login-password]").fill("wrong-password")
    page.locator("[data-qa=login-button]").click()
    site.step("Tried to log in")
    expect(page.get_by_text("Your email or password is incorrect!")).to_be_visible()


# ---------------------------------------------------------------- Demoblaze


def test_demoblaze_add_to_cart(site: BasePage) -> None:
    page = site.page
    site.goto_path("https://www.demoblaze.com/")
    page.get_by_role("link", name="Samsung galaxy s6").click()
    expect(page.locator("h2.name")).to_have_text("Samsung galaxy s6")
    messages: list[str] = []
    page.once("dialog", lambda d: (messages.append(d.message), d.accept()))
    with page.expect_event("dialog"):
        page.get_by_role("link", name="Add to cart").click()
    assert messages == ["Product added."] or messages == ["Product added"], messages
    page.locator("#cartur").click()
    site.step("Opened the cart")
    expect(page.locator("#tbodyid").get_by_text("Samsung galaxy s6").first).to_be_visible(timeout=20_000)


# ---------------------------------------------------------------- ParaBank


def test_parabank_rejects_empty_login(site: BasePage) -> None:
    page = site.page
    site.goto_path("https://parabank.parasoft.com/parabank/index.htm")
    page.locator("input[type=submit][value='Log In']").click()
    site.step("Submitted an empty login")
    expect(page.locator("#rightPanel .error")).to_contain_text("Please enter a username and password.")


# ---------------------------------------------------------------- testautomationpractice.blogspot.com


def test_testautomationpractice_form_controls(site: BasePage) -> None:
    page = site.page
    site.goto_path("https://testautomationpractice.blogspot.com/")
    page.locator("#name").fill("Test User")
    page.locator("#email").fill("test.user@example.com")
    page.locator("#male").check()
    page.locator("#monday").check()
    page.locator("#country").select_option("canada")
    site.step("Filled the form controls")
    expect(page.locator("#male")).to_be_checked()
    expect(page.locator("#monday")).to_be_checked()
    expect(page.locator("#country")).to_have_value("canada")
