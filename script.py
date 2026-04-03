import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ARTIFACTS_DIR = Path("artifacts")
ARTIFACTS_DIR.mkdir(exist_ok=True)


def log(message: str) -> None:
    print(message, flush=True)


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def save_debug(page, name: str) -> None:
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)
    screenshot_path = ARTIFACTS_DIR / f"{safe_name}.png"
    html_path = ARTIFACTS_DIR / f"{safe_name}.html"

    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception as exc:
        log(f"Could not save screenshot: {exc}")

    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        log(f"Could not save html: {exc}")


def first_working_locator(locators):
    for locator in locators:
        try:
            count = locator.count()
            if count > 0:
                first = locator.first
                if first.is_visible():
                    return first
        except Exception:
            continue
    return None


def open_login_page(page) -> None:
    log("Opening Naukri login page...")
    page.goto("https://my.naukri.com/", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(4000)
    save_debug(page, "01_login_page_loaded")


def do_login(page, email: str, password: str) -> None:
    email_input = first_working_locator([
        page.get_by_label(re.compile(r"email|username", re.I)),
        page.get_by_placeholder(re.compile(r"email|username", re.I)),
        page.locator("input[type='email']"),
        page.locator("input[type='text']")
    ])

    password_input = first_working_locator([
        page.get_by_label(re.compile(r"password", re.I)),
        page.get_by_placeholder(re.compile(r"password", re.I)),
        page.locator("input[type='password']")
    ])

    if not email_input:
        raise RuntimeError("Could not find email input field.")
    if not password_input:
        raise RuntimeError("Could not find password input field.")

    log("Filling credentials...")
    email_input.fill(email)
    password_input.fill(password)
    save_debug(page, "02_credentials_filled")

    login_button = first_working_locator([
        page.get_by_role("button", name=re.compile(r"login", re.I)),
        page.locator("button[type='submit']"),
        page.locator("input[type='submit']"),
        page.locator("button").filter(has_text=re.compile(r"login", re.I))
    ])

    if not login_button:
        raise RuntimeError("Could not find login button.")

    log("Clicking login...")
    login_button.click()
    page.wait_for_timeout(8000)
    save_debug(page, "03_after_login_click")


def check_for_verification(page) -> None:
    try:
        body_text = page.locator("body").inner_text(timeout=10000).lower()
    except Exception:
        body_text = ""

    verification_keywords = [
        "otp",
        "captcha",
        "verify",
        "one time password",
        "use otp to login"
    ]

    for keyword in verification_keywords:
        if keyword in body_text:
            raise RuntimeError(f"Verification step detected: {keyword}")


def open_profile(page) -> None:
    log("Opening profile page...")
    page.goto("https://www.naukri.com/mnjuser/profile", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(6000)
    save_debug(page, "04_profile_page_loaded")

    try:
        body_text = page.locator("body").inner_text(timeout=10000).lower()
    except Exception:
        body_text = ""

    if "password" in body_text and "login" in body_text and ("email" in body_text or "username" in body_text):
        raise RuntimeError("Redirected back to login page. Login failed or session not created.")


def open_headline_section(page) -> None:
    log("Trying to open Resume Headline section...")

    possible_click_targets = [
        page.get_by_text(re.compile(r"resume headline", re.I)),
        page.locator("span, div, a, button").filter(has_text=re.compile(r"resume headline", re.I)),
        page.locator("span, div, a, button").filter(has_text=re.compile(r"headline", re.I)),
    ]

    for locator in possible_click_targets:
        try:
            if locator.count() > 0:
                locator.first.click(timeout=5000)
                page.wait_for_timeout(2500)
                save_debug(page, "05_headline_section_clicked")
                return
        except Exception:
            continue

    edit_buttons = [
        page.get_by_role("button", name=re.compile(r"edit", re.I)),
        page.locator("a, button, span, div").filter(has_text=re.compile(r"edit", re.I)),
    ]

    for locator in edit_buttons:
        try:
            if locator.count() > 0:
                locator.first.click(timeout=5000)
                page.wait_for_timeout(2500)
                save_debug(page, "05_generic_edit_clicked")
                return
        except Exception:
            continue

    raise RuntimeError("Could not open resume headline editor.")


def update_headline(page) -> str:
    editor = first_working_locator([
        page.locator("textarea"),
        page.locator("[contenteditable='true']")
    ])

    if not editor:
        raise RuntimeError("Could not find editable field.")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new_text = f"Actively looking for opportunities | Updated {timestamp}"

    log(f"Updating text to: {new_text}")

    try:
        editor.fill(new_text)
    except Exception:
        editor.click()
        page.wait_for_timeout(500)
        try:
            editor.press("Control+A")
        except Exception:
            pass
        editor.type(new_text, delay=20)

    page.wait_for_timeout(1000)
    save_debug(page, "06_text_updated")
    return new_text


def click_save(page) -> None:
    log("Trying to save changes...")

    save_button = first_working_locator([
        page.get_by_role("button", name=re.compile(r"save", re.I)),
        page.locator("button").filter(has_text=re.compile(r"save", re.I)),
        page.locator("text=Save")
    ])

    if not save_button:
        raise RuntimeError("Could not find Save button.")

    save_button.click()
    page.wait_for_timeout(5000)
    save_debug(page, "07_after_save")


def main() -> int:
    email = env_required("NAUKRI_EMAIL")
    password = env_required("NAUKRI_PASSWORD")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        context = browser.new_context(
            viewport={"width": 1440, "height": 2200},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()
        page.set_default_timeout(30000)

        try:
            open_login_page(page)
            do_login(page, email, password)
            check_for_verification(page)
            open_profile(page)
            open_headline_section(page)
            updated_text = update_headline(page)
            click_save(page)

            log("Profile update completed successfully.")
            log(f"Final headline text: {updated_text}")
            return 0

        except PlaywrightTimeoutError as exc:
            save_debug(page, "timeout_error")
            log(f"Timeout error: {exc}")
            return 1

        except Exception as exc:
            save_debug(page, "general_error")
            log(f"Error: {exc}")
            return 1

        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
