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
        log(f"Could not save screenshot for {name}: {exc}")

    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        log(f"Could not save html for {name}: {exc}")


def first_visible_in_scope(scope, builders):
    for builder in builders:
        try:
            locator = builder(scope)
            if locator.count() > 0 and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def list_frames(page) -> None:
    log("Available frames:")
    for i, frame in enumerate(page.frames):
        try:
            log(f"  Frame {i}: name={frame.name!r} url={frame.url}")
        except Exception:
            log(f"  Frame {i}: <unavailable>")


def find_login_elements(page):
    email_builders = [
        lambda s: s.get_by_label(re.compile(r"email|username", re.I)),
        lambda s: s.get_by_placeholder(re.compile(r"email|username", re.I)),
        lambda s: s.locator("input[type='email']"),
        lambda s: s.locator("input[name*='email' i]"),
        lambda s: s.locator("input[name*='user' i]"),
        lambda s: s.locator("input[id*='email' i]"),
        lambda s: s.locator("input[id*='user' i]"),
        lambda s: s.locator("input[type='text']"),
    ]

    password_builders = [
        lambda s: s.get_by_label(re.compile(r"password", re.I)),
        lambda s: s.get_by_placeholder(re.compile(r"password", re.I)),
        lambda s: s.locator("input[type='password']"),
        lambda s: s.locator("input[name*='pass' i]"),
        lambda s: s.locator("input[id*='pass' i]"),
    ]

    login_builders = [
        lambda s: s.get_by_role("button", name=re.compile(r"login", re.I)),
        lambda s: s.locator("button[type='submit']"),
        lambda s: s.locator("input[type='submit']"),
        lambda s: s.locator("button").filter(has_text=re.compile(r"login", re.I)),
        lambda s: s.locator("[role='button']").filter(has_text=re.compile(r"login", re.I)),
    ]

    email_input = first_visible_in_scope(page, email_builders)
    password_input = first_visible_in_scope(page, password_builders)
    login_button = first_visible_in_scope(page, login_builders)

    if email_input and password_input and login_button:
        log("Found login elements on main page.")
        return page, email_input, password_input, login_button

    for idx, frame in enumerate(page.frames):
        try:
            email_input = first_visible_in_scope(frame, email_builders)
            password_input = first_visible_in_scope(frame, password_builders)
            login_button = first_visible_in_scope(frame, login_builders)

            if email_input and password_input and login_button:
                log(f"Found login elements inside frame {idx}: {frame.url}")
                return frame, email_input, password_input, login_button
        except Exception:
            continue

    return None, None, None, None


def open_login_page(page) -> None:
    log("Opening Naukri login page...")
    page.goto("https://my.naukri.com/", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(8000)
    save_debug(page, "01_login_page_loaded")
    list_frames(page)


def do_login(page, email_value: str, password_value: str) -> None:
    scope, email_input, password_input, login_button = find_login_elements(page)

    if not email_input:
        raise RuntimeError("Could not find email input field in page or frames.")
    if not password_input:
        raise RuntimeError("Could not find password input field in page or frames.")
    if not login_button:
        raise RuntimeError("Could not find login button in page or frames.")

    log("Filling credentials...")
    email_input.fill(email_value)
    password_input.fill(password_value)
    page.wait_for_timeout(1000)
    save_debug(page, "02_credentials_filled")

    log("Clicking login...")
    login_button.click()
    page.wait_for_timeout(10000)
    save_debug(page, "03_after_login_click")
    list_frames(page)


def check_for_verification(page) -> None:
    text_parts = []

    try:
        text_parts.append(page.locator("body").inner_text(timeout=5000).lower())
    except Exception:
        pass

    for frame in page.frames:
        try:
            text_parts.append(frame.locator("body").inner_text(timeout=3000).lower())
        except Exception:
            continue

    body_text = "\n".join(text_parts)

    keywords = [
        "otp",
        "captcha",
        "verify",
        "one time password",
        "use otp to login",
    ]

    for keyword in keywords:
        if keyword in body_text:
            raise RuntimeError(f"Verification step detected: {keyword}")


def open_profile(page) -> None:
    log("Opening profile page...")
    page.goto("https://www.naukri.com/mnjuser/profile", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(8000)
    save_debug(page, "04_profile_page_loaded")

    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        pass

    if "password" in body_text and "login" in body_text and ("email" in body_text or "username" in body_text):
        raise RuntimeError("Redirected back to login page. Login failed or session not created.")


def open_headline_section(page) -> None:
    log("Trying to open Resume Headline section...")

    click_targets = [
        page.get_by_text(re.compile(r"resume headline", re.I)),
        page.locator("span, div, a, button").filter(has_text=re.compile(r"resume headline", re.I)),
        page.locator("span, div, a, button").filter(has_text=re.compile(r"headline", re.I)),
        page.locator("span, div, a, button").filter(has_text=re.compile(r"profile summary", re.I)),
    ]

    for locator in click_targets:
        try:
            if locator.count() > 0:
                locator.first.click(timeout=5000)
                page.wait_for_timeout(3000)
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
                page.wait_for_timeout(3000)
                save_debug(page, "05_generic_edit_clicked")
                return
        except Exception:
            continue

    raise RuntimeError("Could not open resume headline editor.")


def find_editor(page):
    candidates = [
        page.locator("textarea"),
        page.locator("[contenteditable='true']"),
        page.locator("div[role='textbox']"),
    ]

    for locator in candidates:
        try:
            if locator.count() > 0 and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def update_headline(page) -> str:
    editor = find_editor(page)
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

    save_candidates = [
        page.get_by_role("button", name=re.compile(r"save", re.I)),
        page.locator("button").filter(has_text=re.compile(r"save", re.I)),
        page.locator("text=Save"),
    ]

    for locator in save_candidates:
        try:
            if locator.count() > 0 and locator.first.is_visible():
                locator.first.click()
                page.wait_for_timeout(5000)
                save_debug(page, "07_after_save")
                return
        except Exception:
            continue

    raise RuntimeError("Could not find Save button.")


def main() -> int:
    email = env_required("NAUKRI_EMAIL")
    password = env_required("NAUKRI_PASSWORD")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
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
