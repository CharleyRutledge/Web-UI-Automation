"""https://www.saucedemo.com — e-commerce login, cart and checkout, plus its deliberately faulty users."""

from __future__ import annotations

from playwright.sync_api import expect

from pages.base_page import BasePage

URL = "https://www.saucedemo.com/"


def login(site: BasePage, user: str, password: str = "secret_sauce") -> None:
    site.goto_path(URL)
    site.page.locator("[data-test=username]").fill(user)
    site.page.locator("[data-test=password]").fill(password)
    site.step(f"Log in as {user}")
    site.page.locator("[data-test=login-button]").click()


def test_full_checkout(site: BasePage) -> None:
    page = site.page
    login(site, "standard_user")
    expect(page.locator("[data-test=title]")).to_have_text("Products")
    page.locator("[data-test=add-to-cart-sauce-labs-backpack]").click()
    page.locator("[data-test=add-to-cart-sauce-labs-bike-light]").click()
    site.step("Added 2 items")
    expect(page.locator("[data-test=shopping-cart-badge]")).to_have_text("2")
    page.locator("[data-test=shopping-cart-link]").click()
    expect(page.locator("[data-test=inventory-item]")).to_have_count(2)
    page.locator("[data-test=checkout]").click()
    page.locator("[data-test=firstName]").fill("Test")
    page.locator("[data-test=lastName]").fill("User")
    page.locator("[data-test=postalCode]").fill("D02 X285")
    site.step("Filled in delivery details")
    page.locator("[data-test=continue]").click()
    expect(page.locator("[data-test=total-label]")).to_contain_text("Total: $")
    page.locator("[data-test=finish]").click()
    site.step("Order placed")
    expect(page.locator("[data-test=complete-header]")).to_have_text("Thank you for your order!")


def test_checkout_requires_details(site: BasePage) -> None:
    page = site.page
    login(site, "standard_user")
    page.locator("[data-test=shopping-cart-link]").click()
    page.locator("[data-test=checkout]").click()
    page.locator("[data-test=continue]").click()
    site.step("Continue with empty form")
    expect(page.locator("[data-test=error]")).to_contain_text("First Name is required")


def test_locked_out_user_is_refused(site: BasePage) -> None:
    login(site, "locked_out_user")
    site.step("Assert refused")
    expect(site.page.locator("[data-test=error]")).to_contain_text("Sorry, this user has been locked out.")


def test_wrong_password_is_refused(site: BasePage) -> None:
    login(site, "standard_user", "not-the-password")
    expect(site.page.locator("[data-test=error]")).to_contain_text("Username and password do not match")


def test_sort_by_price_low_to_high(site: BasePage) -> None:
    page = site.page
    login(site, "standard_user")
    page.locator("[data-test=product-sort-container]").select_option("lohi")
    site.step("Sorted by price")
    prices = [float(p.removeprefix("$")) for p in page.locator("[data-test=inventory-item-price]").all_inner_texts()]
    assert prices and prices == sorted(prices), prices


def test_problem_user_shows_wrong_images(site: BasePage) -> None:
    """problem_user is a deliberately buggy account: every product shows the same image. Detect it."""
    login(site, "problem_user")
    sources = site.page.locator("img.inventory_item_img").evaluate_all("els => els.map(e => e.getAttribute('src'))")
    site.step("Collected product images")
    assert len(sources) == 6
    assert len(set(sources)) == 1, "expected problem_user's known bug (all images identical) — has the site changed?"
