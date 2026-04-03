import json
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


def save_text(name: str, content: str) -> None:
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)
    (ARTIFACTS_DIR / f"{safe_name}.txt").write_text(content, encoding="utf-8")


def save_json(name: str, obj) -> None:
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)
    (ARTIFACTS_DIR / f"{safe_name}.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def save_debug(page, name: str) -> None:
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)

    try:
        page.screenshot(path=str(ARTIFACTS_DIR / f"{safe_name}.png"), full_page=True)
    except Exception as exc:
        log(f"Could not save screenshot for {name}: {exc}")

    try:
        (ARTIFACTS_DIR / f"{safe_name}.html").write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        log(f"Could not save html for {name}: {exc}")


def dump_dom_summary(page, prefix: str) -> None:
    try:
        title = page.title()
    except Exception:
        title = ""

    try:
        url = page.url
    except Exception:
        url = ""

    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        body_text = ""

    try:
        inputs = page.evaluate("""
        () => Array.from(document.querySelectorAll('input, textarea, button')).map((el, i) => ({
          index: i,
          tag: el.tagName,
          type: el.getAttribute('type'),
          id: el.id,
          name: el.getAttribute('name'),
          placeholder: el.getAttribute('placeholder'),
          ariaLabel: el.getAttribute('aria-label'),
          role: el.getAttribute('role'),
          value: (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') ? el.value : null,
          text: (el.innerText || el.textContent || '').trim().slice(0, 120),
          visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
        }))
        """)
    except Exception as exc:
        inputs = [{"error": str(exc)}]

    save_text(f"{prefix}_meta", f"URL: {url}\nTITLE: {title}\n\nBODY:\n{body_text[:8000]}")
    save_json(f"{prefix}_controls", inputs)


def open_login_page(page) -> None:
    log("Opening Naukri login page...")
    page.goto("https://my.naukri.com/", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(12000)
    save_debug(page, "01_login_page_loaded")
    dump_dom_summary(page, "01_login_page_loaded")


def detect_verification(page) -> None:
    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        pass

    keywords = [
        "otp",
        "captcha",
        "verify",
        "one time password",
        "use otp to login",
        "sign in with google",
    ]
    for keyword in keywords:
        if keyword in body_text:
            log(f"Detected keyword on page: {keyword}")


def fill_login_with_js(page, email: str, password: str) -> bool:
    js = """
    ([emailValue, passwordValue]) => {
      const all = Array.from(document.querySelectorAll('input, textarea'));

      const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);

      const scoreEmail = (el) => {
        const txt = [
          el.type || '',
          el.name || '',
          el.id || '',
          el.placeholder || '',
          el.getAttribute('aria-label') || '',
          el.outerHTML || ''
        ].join(' ').toLowerCase();

        let score = 0;
        if (!visible(el)) score -= 5;
        if (el.type === 'email') score += 10;
        if (el.type === 'text') score += 2;
        if (txt.includes('email')) score += 8;
        if (txt.includes('user')) score += 5;
        if (txt.includes('username')) score += 8;
        if (txt.includes('login')) score += 2;
        return score;
      };

      const scorePassword = (el) => {
        const txt = [
          el.type || '',
          el.name || '',
          el.id || '',
          el.placeholder || '',
          el.getAttribute('aria-label') || '',
          el.outerHTML || ''
        ].join(' ').toLowerCase();

        let score = 0;
        if (!visible(el)) score -= 5;
        if (el.type === 'password') score += 20;
        if (txt.includes('password')) score += 10;
        if (txt.includes('pass')) score += 5;
        return score;
      };

      const emailInput = all
        .map(el => ({ el, score: scoreEmail(el) }))
        .sort((a, b) => b.score - a.score)[0];

      const passwordInput = all
        .map(el => ({ el, score: scorePassword(el) }))
        .sort((a, b) => b.score - a.score)[0];

      if (!emailInput || !passwordInput) {
        return { ok: false, reason: 'no candidates' };
      }

      if (emailInput.score < 1 || passwordInput.score < 5) {
        return {
          ok: false,
          reason: 'weak candidates',
          emailScore: emailInput.score,
          passwordScore: passwordInput.score
        };
      }

      emailInput.el.focus();
      emailInput.el.value = emailValue;
      emailInput.el.dispatchEvent(new Event('input', { bubbles: true }));
      emailInput.el.dispatchEvent(new Event('change', { bubbles: true }));

      passwordInput.el.focus();
      passwordInput.el.value = passwordValue;
      passwordInput.el.dispatchEvent(new Event('input', { bubbles: true }));
      passwordInput.el.dispatchEvent(new Event('change', { bubbles: true }));

      const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], [role="button"]'));
      const loginBtn = buttons.find(el => {
        const txt = ((el.innerText || el.textContent || '') + ' ' + (el.value || '')).toLowerCase();
        return txt.includes('login') || txt.includes('log in') || txt.includes('sign in');
      });

      return {
        ok: true,
        clicked: !!loginBtn
      };
    }
    """
    result = page.evaluate(js, [email, password])
    save_json("02_fill_login_result", result)
    return bool(result.get("ok"))


def click_login_with_js(page) -> bool:
    js = """
    () => {
      const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
      const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], [role="button"], a'));
      const btn = buttons.find(el => {
        const txt = ((el.innerText || el.textContent || '') + ' ' + (el.value || '')).toLowerCase();
        return visible(el) && (txt.includes('login') || txt.includes('log in') || txt.includes('sign in'));
      });

      if (!btn) return { ok: false, reason: 'login button not found' };

      btn.click();
      return { ok: true, text: (btn.innerText || btn.textContent || btn.value || '').trim() };
    }
    """
    result = page.evaluate(js)
    save_json("03_click_login_result", result)
    return bool(result.get("ok"))


def do_login(page, email: str, password: str) -> None:
    log("Trying JS-based login field detection...")
    if not fill_login_with_js(page, email, password):
        dump_dom_summary(page, "02_login_not_found")
        raise RuntimeError("Could not find login inputs in rendered DOM.")

    save_debug(page, "02_credentials_filled")
    detect_verification(page)

    log("Trying JS-based login button click...")
    if not click_login_with_js(page):
        dump_dom_summary(page, "03_login_button_not_found")
        raise RuntimeError("Could not find login button in rendered DOM.")

    page.wait_for_timeout(12000)
    save_debug(page, "03_after_login_click")
    dump_dom_summary(page, "03_after_login_click")
    detect_verification(page)


def ensure_logged_in(page) -> None:
    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        pass

    if any(x in body_text for x in ["otp", "captcha", "verify", "use otp to login"]):
        raise RuntimeError("Verification page detected after login.")

    log("Opening profile page...")
    page.goto("https://www.naukri.com/mnjuser/profile", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(10000)
    save_debug(page, "04_profile_page_loaded")
    dump_dom_summary(page, "04_profile_page_loaded")

    try:
        body_text = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        body_text = ""

    if "login" in body_text and "password" in body_text:
        raise RuntimeError("Redirected back to login page. Session not established.")


def open_headline_section(page) -> None:
    targets = [
        "text=/resume headline/i",
        "text=/headline/i",
        "text=/profile summary/i",
    ]
    for selector in targets:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                locator.first.click(timeout=5000)
                page.wait_for_timeout(3000)
                save_debug(page, "05_open_editor")
                dump_dom_summary(page, "05_open_editor")
                return
        except Exception:
            continue

    try:
        edit_btn = page.get_by_role("button", name=re.compile(r"edit", re.I))
        if edit_btn.count() > 0:
            edit_btn.first.click(timeout=5000)
            page.wait_for_timeout(3000)
            save_debug(page, "05_open_editor")
            dump_dom_summary(page, "05_open_editor")
            return
    except Exception:
        pass

    raise RuntimeError("Could not open headline/profile editor.")


def update_text(page) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new_text = f"Actively looking for opportunities | Updated {timestamp}"

    js = """
    (value) => {
      const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
      const candidates = Array.from(document.querySelectorAll('textarea, [contenteditable="true"], div[role="textbox"]'));
      const el = candidates.find(visible);
      if (!el) return { ok: false };

      if (el.tagName === 'TEXTAREA') {
        el.focus();
        el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      } else {
        el.focus();
        el.textContent = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }

      return { ok: true, tag: el.tagName };
    }
    """
    result = page.evaluate(js, new_text)
    save_json("06_update_text_result", result)

    if not result.get("ok"):
        raise RuntimeError("Could not find editable field.")

    page.wait_for_timeout(1500)
    save_debug(page, "06_text_updated")
    return new_text


def click_save(page) -> None:
    js = """
    () => {
      const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
      const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], [role="button"]'));
      const btn = buttons.find(el => {
        const txt = ((el.innerText || el.textContent || '') + ' ' + (el.value || '')).toLowerCase();
        return visible(el) && txt.includes('save');
      });
      if (!btn) return { ok: false };
      btn.click();
      return { ok: true, text: (btn.innerText || btn.textContent || btn.value || '').trim() };
    }
    """
    result = page.evaluate(js)
    save_json("07_click_save_result", result)

    if not result.get("ok"):
        raise RuntimeError("Could not find Save button.")

    page.wait_for_timeout(5000)
    save_debug(page, "07_after_save")
    dump_dom_summary(page, "07_after_save")


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
            ensure_logged_in(page)
            open_headline_section(page)
            updated = update_text(page)
            click_save(page)

            log("Profile update completed successfully.")
            log(f"Final text: {updated}")
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
