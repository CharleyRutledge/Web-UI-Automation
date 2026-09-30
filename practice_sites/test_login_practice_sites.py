"""Login pages built for practice: practicetestautomation.com, practice.expandtesting.com, OrangeHRM demo."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from pages.base_page import BasePage

PTA = "https://practicetestautomation.com/practice-test-login/"
EXPAND = "https://practice.expandtesting.com/login"
ORANGE = "https://opensource-demo.orangehrmlive.com/web/index.php/auth/login"


def test_practicetestautomation_valid_login(site: BasePage) -> None:
    page = site.page
    site.goto_path(PTA)
    page.locator("#username").fill("student")
    page.locator("#password").fill("Password123")
    site.click_role("button", name="Submit", step_label="Submit")
    expect(page).to_have_url(re.compile("logged-in-successfully"))
    site.expect_heading("Logged In Successfully")
    expect(page.get_by_role("link", name="Log out")).to_be_visible()


@pytest.mark.parametrize(
    "user, password, error",
    [("incorrectUser", "Password123", "Your username is invalid!"), ("student", "incorrectPassword", "Your password is invalid!")],
    ids=["wrong username", "wrong password"],
)
def test_practicetestautomation_rejects(site: BasePage, user: str, password: str, error: str) -> None:
    page = site.page
    site.goto_path(PTA)
    page.locator("#username").fill(user)
    page.locator("#password").fill(password)
    site.click_role("button", name="Submit", step_label="Submit")
    expect(page.locator("#error")).to_have_text(error)


def test_expandtesting_login_and_logout(site: BasePage) -> None:
    page = site.page
    site.goto_path(EXPAND)
    page.locator("#username").fill("practice")
    page.locator("#password").fill("SuperSecretPassword!")
    page.locator("#login button[type=submit]").click()
    site.step("Logged in")
    expect(page.locator("#flash")).to_contain_text("You logged into a secure area!")
    page.get_by_role("link", name="Logout").click()
    expect(page.locator("#flash")).to_contain_text("You logged out of the secure area!")


def test_expandtesting_rejects_wrong_password(site: BasePage) -> None:
    page = site.page
    site.goto_path(EXPAND)
    page.locator("#username").fill("practice")
    page.locator("#password").fill("wrong")
    page.locator("#login button[type=submit]").click()
    site.step("Assert error")
    expect(page.locator("#flash")).to_contain_text("Your password is invalid!")


def test_orangehrm_admin_login(site: BasePage) -> None:
    page = site.page
    site.goto_path(ORANGE)
    page.locator("input[name=username]").fill("Admin")
    page.locator("input[name=password]").fill("admin123")
    site.click_role("button", name="Login", step_label="Click Login")
    expect(page).to_have_url(re.compile("/dashboard"), timeout=45_000)
    site.expect_heading("Dashboard")


def test_orangehrm_rejects_bad_credentials(site: BasePage) -> None:
    page = site.page
    site.goto_path(ORANGE)
    page.locator("input[name=username]").fill("Admin")
    page.locator("input[name=password]").fill("nope")
    site.click_role("button", name="Login", step_label="Click Login")
    expect(page.get_by_role("alert")).to_contain_text("Invalid credentials", timeout=45_000)
