import os
import sys
import re
from datetime import datetime, timezone
from pathlib import Path
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


def bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "")
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def save_debug(page, name: str) -> None:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)
    png_path = ARTIFACTS_DIR / f"{safe}.png"
    html_path = ARTIFACTS_DIR / f"{safe}.html"
    try:
        page.screenshot(path=str(png_path), full_page=True)
    except Exception as exc:
        log(f"Could not save screenshot {png_path}: {exc}")
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        log(f"Could not save html {html_path}: {exc}")


def first_visible(locator_list):
    for locator in locator_list:
        try:
            if locator.count() > 0 and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def click_if_visible(locator) -> bool:
    try:
        if locator and locator.is_visible():
            locator.click()
            return True
    except Exception:
        return False
    return False


def fill_login(page, email: str, password: str) -> None:
    log("Opening Naukri home page...")
    page.goto("https://www.naukri.com/", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(3000)
    save_debug(page, "01_home")

    # Try common login entry points.
    login_candidates = [
        page.get_by_role("link", name=re.compile(r"login", re.I)),
        page.get_by_role("button", name=re.compile(r"login", re.I)),
        page.locator("#login_Layer"),
        page.locator("text=Login"),
    ]
    login_button = first_visible(login_candidates)
    if not login_button:
        raise RuntimeError("Could not find the Login button/link on the home page.")
    log("Clicking login...")
    login_button.click()
    page.wait_for_timeout(3000)
    save_debug(page, "02_login_opened")

    # Naukri login publicly shows fields labeled Email ID / Username and Password.
    email_candidates = [
        page.get_by_label(re.compile(r"email id|username", re.I)),
        page.get_by_placeholder(re.compile(r"email id|username", re.I)),
        page.locator("input[type='text']").filter(has_not_text=""),
        page.locator("input[type='email']"),
    ]
    password_candidates = [
        page.get_by_label(re.compile(r"password", re.I)),
        page.get_by_placeholder(re.compile(r"password", re.I)),
        page.locator("input[type='password']"),
    ]

    email_input = first_visible(email_candidates)
    password_input = first_visible(password_candidates)

    if not email_input or not password_input:
        raise RuntimeError("Could not find login input fields.")

    log("Filling credentials...")
    email_input.fill(email)
    password_input.fill(password)
    save_debug(page, "03_login_filled")

    submit_candidates = [
        page.get_by_role("button", name=re.compile(r"login", re.I)),
        page.locator("button[type='submit']"),
        page.locator("button").filter(has_text=re.compile(r"login", re.I)),
    ]
    submit_button = first_visible(submit_candidates)
    if not submit_button:
        raise RuntimeError("Could not find login submit button.")

    log("Submitting login...")
    submit_button.click()
    page.wait_for_timeout(8000)
    save_debug(page, "04_after_login_submit")


def ensure_logged_in(page) -> None:
    text = page.locator("body").inner_text(timeout=10000)
    lower = text.lower()

    if "use otp to login" in lower and "password" in lower:
        # Still on login form, probably bad credentials.
        raise RuntimeError("Still on login page after submit. Check credentials.")

    if "captcha" in lower or "verify" in lower or "otp" in lower:
        raise RuntimeError(
            "Naukri requested OTP/CAPTCHA/verification. Manual intervention is needed."
        )

    # Try to go to profile page directly.
    log("Navigating to profile page...")
    page.goto("https://www.naukri.com/mnjuser/profile", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(5000)
    save_debug(page, "05_profile_page")

    body = page.locator("body").inner_text(timeout=10000).lower()
    if "login" in body and "password" in body and "email id" in body:
        raise RuntimeError("Navigation redirected back to login; session was not established.")


def open_headline_editor(page):
    # Different versions of the profile UI can show Resume headline / Profile summary.
    openers = [
        page.get_by_text(re.compile(r"resume headline", re.I)),
        page.get_by_text(re.compile(r"profile summary", re.I)),
        page.get_by_role("button", name=re.compile(r"edit", re.I)),
        page.locator("span, div, a, button").filter(has_text=re.compile(r"resume headline", re.I)),
    ]

    for opener in openers:
        try:
            if opener.count() > 0:
                opener.first.click(timeout=5000)
                page.wait_for_timeout(2500)
                save_debug(page, "06_editor_open")
                return
        except Exception:
            continue

    raise RuntimeError("Could not open profile editor for headline/summary.")


def update_textarea(page, suffix: str) -> str:
    textareas = [
        page.locator("textarea"),
        page.locator("[contenteditable='true']"),
    ]

    editor = None
    for candidate in textareas:
        try:
            if candidate.count() > 0 and candidate.first.is_visible():
                editor = candidate.first
                break
        except Exception:
            continue

    if not editor:
        raise RuntimeError("Could not find editable textarea/contenteditable field.")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    stamp = f"{suffix} | {ts}".strip()

    current_text = ""
    try:
        current_text = editor.input_value(timeout=3000)
    except Exception:
        try:
            current_text = editor.inner_text(timeout=3000)
        except Exception:
            current_text = ""

    current_text = current_text.strip()

    # Keep the text bounded so repeated runs don't make it grow forever.
    if current_text:
        base = re.sub(r"\s*\|\s*Updated.*$", "", current_text, flags=re.I).strip()
        new_text = f"{base} | Updated {ts}"
    else:
        new_text = f"Actively looking for opportunities | {stamp}"

    try:
        editor.click()
        try:
            editor.fill(new_text)
        except Exception:
            editor.press("Control+A")
            editor.type(new_text, delay=20)
    except Exception as exc:
        raise RuntimeError(f"Failed to write updated text: {exc}") from exc

    save_debug(page, "07_text_updated")
    return new_text


def click_save(page) -> None:
    save_candidates = [
        page.get_by_role("button", name=re.compile(r"save", re.I)),
        page.locator("button").filter(has_text=re.compile(r"save", re.I)),
        page.locator("text=Save"),
    ]
    save_button = first_visible(save_candidates)
    if not save_button:
        raise RuntimeError("Could not find Save button.")

    log("Saving changes...")
    save_button.click()
    page.wait_for_timeout(5000)
    save_debug(page, "08_after_save")


def main() -> int:
    email = env_required("NAUKRI_EMAIL")
    password = env_required("NAUKRI_PASSWORD")
    headline_suffix = os.getenv("HEADLINE_SUFFIX", "Updated manually from GitHub Actions").strip()
    dry_run = bool_env("DRY_RUN", default=False)

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
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        try:
            fill_login(page, email, password)
            ensure_logged_in(page)

            if dry_run:
                log("DRY_RUN=true, stopping after successful login/navigation.")
                browser.close()
                return 0

            open_headline_editor(page)
            new_text = update_textarea(page, headline_suffix)
            click_save(page)

            log(f"Profile update attempted successfully. New text: {new_text}")
            browser.close()
            return 0

        except PlaywrightTimeoutError as exc:
            save_debug(page, "timeout_error")
            log(f"Timeout error: {exc}")
            browser.close()
            return 1
        except Exception as exc:
            save_debug(page, "general_error")
            log(f"Error: {exc}")
            browser.close()
            return 1


if __name__ == "__main__":
    sys.exit(main())
