"""Pollinations Postcard Starter: one prompt makes an image, a caption, and narration.

Sign-in is Bring Your Own Pollen, via the Pollinations device flow. The visitor
approves a short code and the browser keeps the returned key in sessionStorage;
the server never stores it.

No local model: every generation call goes to gen.pollinations.ai with the
visitor's key, so a CPU Basic duplicate is enough.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import gradio as gr
import requests

# ---------------------------------------------------------------------------
# Optional ZeroGPU support: use the real decorator when it is installed,
# otherwise a passthrough, so the same code runs on CPU Basic and on a
# ZeroGPU duplicate. Keep suggested_hardware: cpu-basic.
# ---------------------------------------------------------------------------
try:
    import spaces  # type: ignore

    GPU = spaces.GPU
except Exception:  # not a ZeroGPU Space; fall through to the passthrough

    def GPU(*d, **k):
        def wrap(fn):
            return fn

        if len(d) == 1 and callable(d[0]) and not k:
            return d[0]
        return wrap


log = logging.getLogger("postcard")
logging.basicConfig(level=logging.INFO)

GEN_BASE = os.getenv("GEN_BASE", "https://gen.pollinations.ai").rstrip("/")
ENTER_BASE = os.getenv("ENTER_BASE", "https://enter.pollinations.ai").rstrip("/")
APP_KEY = os.getenv("POLLINATIONS_APP_KEY", "").strip()  # pk_... App Key, set as a Space Variable

CACHE_DIR = Path(tempfile.gettempdir()) / "polli_postcards"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Pollinations brand palette.
CREAM = "#F3EBDE"
TAN = "#e8e2d8"
BORDER = "#cfc8b8"
INK = "#110518"
MUTED = "#4a3f5c"
SUBTLE = "#6b5f80"
WHITE = "#ffffff"
LAVENDER = "#E9D9EF"
PERIWINKLE = "#D8DFF8"
MINT = "#D4F0D7"
LIME = "#E8F372"

FALLBACK_TEXT = ["openai/gpt-6-luna", "openai/gpt-5.4-nano", "openai/gpt-5-nano", "openai/gpt-4o-mini"]
FALLBACK_IMAGE = ["black-forest-labs/flux.1-schnell", "flux", "openai/gpt-image-1-mini"]
FALLBACK_VOICES = ["nova", "alloy", "echo", "shimmer", "onyx", "fable"]

STARTERS = [
    "A lighthouse at dawn, gulls, soft fog",
    "A night market in the rain, neon reflections",
    "A small cabin above the clouds at golden hour",
]

# _set_key swaps this with "" so the sign-in link hides while connected.
SIGNIN_HTML = "<a id='polli-signin' href='#'>Sign in with Pollen</a>"

TIMEOUT = 90


# ---------------------------------------------------------------------------
# Catalog: live with fallback, preferred defaults first
# ---------------------------------------------------------------------------
def _get_json(url: str):
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("catalog fetch failed %s: %s", url, e)
        return None


@lru_cache(maxsize=1)
def _catalog_cached(bucket: int):
    del bucket  # hourly cache-busting key; the value itself is unused
    out = {"text": list(FALLBACK_TEXT), "image": list(FALLBACK_IMAGE)}
    for kind, path in (("text", "/text/models"), ("image", "/image/models")):
        data = _get_json(f"{GEN_BASE}{path}")
        if isinstance(data, list) and data:
            ids = [m.get("id") or m.get("name") for m in data if isinstance(m, dict)]
            ids = [i for i in ids if i][:24]
            if ids:
                for d in FALLBACK_TEXT if kind == "text" else FALLBACK_IMAGE:
                    if d not in ids:
                        ids.append(d)
                out[kind] = ids
    for kind, prefs in (("text", FALLBACK_TEXT), ("image", FALLBACK_IMAGE)):
        ids = out[kind]
        for d in reversed(prefs):
            if d in ids:
                ids.remove(d)
                ids.insert(0, d)
    return out


def catalog():
    return _catalog_cached(int(time.time() // 3600))


def _err_message(status: int) -> str:
    if status == 401:
        return "Your key was not accepted (401). Connect Pollen again, then retry."
    if status == 402:
        return "Out of Pollen (402). Add Pollen at enter.pollinations.ai, then retry."
    if status == 429:
        return "Rate limited (429). Wait a few seconds, then retry."
    return f"Pollinations returned {status}. Try again in a moment."


def _save_bytes(data: bytes, suffix: str) -> str:
    tag = base64.urlsafe_b64encode(os.urandom(6)).decode()
    path = CACHE_DIR / f"{int(time.time() * 1000)}_{tag}{suffix}"
    path.write_bytes(data)
    return str(path)


def _require_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Connect Pollen first. Visitors pay with their own key.")
    return key


# ---------------------------------------------------------------------------
# Pollinations calls
# ---------------------------------------------------------------------------
def check_key(api_key: str) -> tuple[str, str]:
    """Validate a visitor key. Returns (username, status line)."""
    key = (api_key or "").strip()
    if not key:
        return "", "Not connected"
    try:
        who = requests.get(
            f"{ENTER_BASE}/api/oauth/userinfo",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
        name = ""
        if who.ok:
            name = (who.json() or {}).get("preferred_username") or ""
        bal = requests.get(
            f"{GEN_BASE}/account/balance",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
        balance = ""
        if bal.ok:
            balance = str((bal.json() or {}).get("balance", ""))
        if not who.ok and not bal.ok:
            return "", "That key was not accepted. Connect Pollen again."
        line = f"Signed in as {name}" if name else "Signed in"
        if balance:
            line += f", {balance} Pollen"
        return name, line
    except Exception as e:
        log.warning("check_key failed: %s", e)
        return "", "Could not reach Pollinations. Check your connection."


def make_caption(prompt: str, api_key: str, text_model: str) -> str:
    key = _require_key(api_key)
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Describe your postcard first.")
    body = {
        "model": text_model or FALLBACK_TEXT[0],
        "messages": [
            {
                "role": "system",
                "content": (
                    "Write a warm postcard caption in exactly two sentences. "
                    "Concrete nouns. No em dashes, no buzzwords, no hashtags, no emoji."
                ),
            },
            {"role": "user", "content": f"Postcard caption for: {prompt[:400]}"},
        ],
        "max_tokens": 160,
    }
    r = requests.post(
        f"{GEN_BASE}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(_err_message(r.status_code))
    try:
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        raise RuntimeError("Could not read the caption. Try again.")


def make_image(prompt: str, api_key: str, image_model: str) -> str:
    key = _require_key(api_key)
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Describe your postcard first.")
    url = (
        f"{GEN_BASE}/image/{quote(prompt[:800])}"
        f"?model={quote(image_model or FALLBACK_IMAGE[0])}&width=1024&height=1024&nologo=true"
    )
    r = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(_err_message(r.status_code))
    if "image" not in r.headers.get("Content-Type", "") and len(r.content) < 2000:
        raise RuntimeError("Could not read the image. Try again.")
    return _save_bytes(r.content, ".jpg")


def make_audio(caption: str, api_key: str, voice: str) -> str:
    key = _require_key(api_key)
    caption = (caption or "").strip()
    if not caption:
        raise ValueError("Generate a caption first.")
    body = {"model": "openai/tts-1", "input": caption[:900], "voice": voice or "nova"}
    r = requests.post(
        f"{GEN_BASE}/v1/audio/speech",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=TIMEOUT,
    )
    if not r.ok or len(r.content) < 1000:
        r = requests.get(
            f"{GEN_BASE}/audio/{quote(caption[:900])}?voice={quote(voice or 'nova')}",
            headers={"Authorization": f"Bearer {key}"},
            timeout=TIMEOUT,
        )
    if not r.ok or len(r.content) < 1000:
        raise RuntimeError(_err_message(r.status_code if not r.ok else 500))
    return _save_bytes(r.content, ".mp3")


# ---------------------------------------------------------------------------
# MCP tool. Typed args plus an Args docstring keep the schema clean.
# ---------------------------------------------------------------------------
@GPU(duration=90)
def make_postcard(
    prompt: str,
    api_key: str,
    image_model: str = "black-forest-labs/flux.1-schnell",
    text_model: str = "openai/gpt-6-luna",
    voice: str = "nova",
) -> tuple[str, str, str]:
    """Make a living postcard from one prompt: image plus caption plus narration.

    Args:
        prompt: The idea for the postcard, one sentence is enough.
        api_key: Visitor Pollen key, spending comes from this key.
        image_model: Image model ID or alias.
        text_model: Text model ID or alias.
        voice: Audio voice name.

    Returns:
        The caption, the path to the image, the path to the narration audio.
    """
    caption = make_caption(prompt, api_key, text_model)
    try:
        image_path = make_image(prompt, api_key, image_model)
    except Exception as e:
        log.warning("image step failed: %s", e)
        image_path = ""
        if "401" in str(e) or "402" in str(e):
            raise
    try:
        audio_path = make_audio(caption, api_key, voice)
    except Exception as e:
        log.warning("audio step failed: %s", e)
        audio_path = ""
    return caption, image_path, audio_path


def ui_generate(prompt, api_key, image_model, text_model, voice):
    prompt = (prompt or "").strip()
    if not prompt:
        msg = "Describe your postcard first, then choose Generate postcard."
        return None, None, None, gr.update(value=""), gr.update(value=msg, visible=True)
    try:
        caption, image_path, audio_path = make_postcard(prompt, api_key, image_model, text_model, voice)
        parts = []
        if image_path:
            parts.append("image")
        parts.append("caption")
        if audio_path:
            parts.append("narration")
        missing = [p for p in ("image", "narration") if p not in parts]
        msg = f"Done: {', '.join(parts)} ready." + (f" Still waiting on {', '.join(missing)}." if missing else "")
        return image_path or None, audio_path or None, caption or None, gr.update(value=""), gr.update(value=msg, visible=True)
    except Exception as e:
        msg = str(e)
        log.warning("generate: %s", msg)
        return None, None, None, gr.update(value=""), gr.update(value=msg, visible=True)


def sign_out(api_key: str):
    return gr.update(value=""), "", "Not connected"


# ---------------------------------------------------------------------------
# Client JS. Sign-in is the Pollinations device flow: request a code, the
# visitor approves it on enter.pollinations.ai, the tab polls until the key
# arrives. No redirects, popups, or cross-tab storage, so it works the same
# in HF's iframe, a normal tab, and on a phone. The key stays in
# sessionStorage and is mirrored into a hidden textbox for Gradio to read.
# ---------------------------------------------------------------------------
APP_JS = r"""
/* All entry points live on window: head-JS runs before Gradio mounts, so bare
   function declarations would never be visible to button handlers. */
/* Public App Key, baked in at build so sign-in works before anything mounts. */
window.POLLI_APP_KEY = "%%POLLI_APP_KEY%%";
window.polliBridge = function (el, key) {
  if (!el) return false;
  el.value = key;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
};
window.polliBridgeEl = function () {
  return document.querySelector('#polli-key-bridge textarea, #polli-key-bridge input');
};
window.polliFillRetry = function (key, tries) {
  if (window.polliBridge(window.polliBridgeEl(), key)) return;
  if ((tries || 0) > 0) setTimeout(() => window.polliFillRetry(key, (tries || 0) - 1), 1000);
};
window.polliToast = function (msg) {
  let t = document.getElementById('polli-toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'polli-toast';
    t.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);'
      + 'background:#110518;color:#ffffff;padding:10px 20px;border-radius:999px;'
      + 'z-index:9999;font-size:14px;font-family:system-ui,sans-serif;';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(window.polliToastTimer);
  window.polliToastTimer = setTimeout(() => { t.style.display = 'none'; }, 5000);
};
window.polliSignin = async function () {
  if (window.polliDevCode || window.polliDevTimer) return;
  let d = null;
  try {
    const res = await fetch('https://enter.pollinations.ai/api/device/code', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        client_id: window.POLLI_APP_KEY,
        scope: 'generate',
        budget: '10',
        expiry: '7',
        models: 'image,text,audio',
      }),
    });
    d = await res.json().catch(() => ({}));
    if (!res.ok || !d.device_code) {
      window.polliToast('Could not start sign-in'
        + (d.error_description ? ': ' + d.error_description
          : (d.error ? ' (' + d.error + ')' : '. Try again.')));
      return;
    }
  } catch (e) {
    window.polliToast('Sign-in request failed. Check your connection, then try again.');
    return;
  }
  window.polliDevCode = d.device_code;
  const codeEl = document.getElementById('polli-usercode');
  const linkEl = document.getElementById('polli-devlink');
  const box = document.getElementById('polli-devecode');
  if (codeEl) codeEl.textContent = d.user_code || '';
  if (linkEl) linkEl.href = d.verification_uri_complete || d.verification_uri || linkEl.href;
  if (box) box.style.display = 'block';
  const deadline = Date.now() + (d.expires_in || 1800) * 1000;
  const every = Math.max(5000, (d.interval || 5) * 1000);
  const tick = async function () {
    if (!window.polliDevCode) return; /* cancelled */
    if (Date.now() > deadline) {
      window.polliStopSignin('That code expired. Click Sign in again.');
      return;
    }
    let t = {};
    let ok = false;
    try {
      const r = await fetch('https://enter.pollinations.ai/api/device/token', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({device_code: window.polliDevCode}),
      });
      t = await r.json().catch(() => ({}));
      ok = r.ok;
    } catch (e) { /* transient network blip; keep polling until the deadline */ }
    if (ok && t.access_token) {
      const token = t.access_token;
      window.polliStopSignin('');
      sessionStorage.setItem('polli_key', token);
      window.polliFillRetry(token, 30);
      window.polliToast('Signed in');
      return;
    }
    const err = t.error || '';
    if (err && err !== 'authorization_pending' && err !== 'slow_down') {
      window.polliStopSignin(
        err === 'access_denied' ? 'Sign-in was denied.'
        : err === 'expired_token' ? 'That code expired. Click Sign in again.'
        : 'Sign-in failed (' + err + '). Try again.');
      return;
    }
    window.polliDevTimer = setTimeout(tick, every);
  };
  window.polliDevTimer = setTimeout(tick, every);
};
window.polliStopSignin = function (msg) {
  clearTimeout(window.polliDevTimer);
  window.polliDevTimer = null;
  window.polliDevCode = null;
  const box = document.getElementById('polli-devecode');
  if (box) box.style.display = 'none';
  if (msg) window.polliToast(msg);
};
window.polliBoot = async function () {
  try {
    /* Legacy fragment flow still works if a visitor arrives with #api_key= */
    const frag = new URLSearchParams((location.hash || '').slice(1));
    const fragKey = frag.get('api_key');
    if (fragKey) {
      sessionStorage.setItem('polli_key', fragKey);
      window.polliFillRetry(fragKey, 30);
      history.replaceState(null, '', location.pathname + location.search);
      return;
    }
    const saved = sessionStorage.getItem('polli_key');
    if (saved) window.polliFillRetry(saved, 30);
  } catch (e) { console.warn('polli boot', e); }
};
window.polliDisconnect = function () {
  sessionStorage.removeItem('polli_key');
  window.polliStopSignin('');
  window.polliBridge(window.polliBridgeEl(), '');
  return true;
};
/* Delegated clicks survive Gradio remounting the injected HTML. */
document.addEventListener('click', function (e) {
  const el = e.target && e.target.closest ? e.target : null;
  if (!el) return;
  if (el.closest('#polli-signin')) {
    e.preventDefault();
    window.polliSignin();
  } else if (el.closest('#polli-copycode')) {
    const codeEl = document.getElementById('polli-usercode');
    const code = codeEl ? codeEl.textContent : '';
    if (navigator.clipboard && code) {
      navigator.clipboard.writeText(code).then(
        () => window.polliToast('Code copied'),
        () => window.polliToast('Could not copy. Select the code manually.'));
    }
  } else if (el.closest('#polli-cancel')) {
    window.polliStopSignin('Sign-in cancelled.');
  }
});
window.polliBoot();
"""
# Bake the public App Key into the JS at build time.
APP_JS = APP_JS.replace("%%POLLI_APP_KEY%%", APP_KEY)

APP_CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Maven+Pro:wght@500;600;700&family=Mako:wght@400;500&display=swap');

html, body {{ color-scheme: light !important; background: {CREAM} !important; }}
.gradio-container {{ background: {CREAM} !important; }}
.gradio-container .prose, .gradio-container .prose * {{ color: {INK} !important; }}

/* Masthead: the one place with a dark surface, so it needs its own colours. */
#masthead {{
  background: {INK}; border-radius: 16px; padding: 26px 28px;
}}
#masthead h1 {{ color: {WHITE} !important; font-size: 28px !important; margin: 0 0 8px !important; }}
#masthead h1 .mark {{ color: {LIME} !important; }}
#masthead p {{ color: #ded3e6 !important; margin: 0 !important; max-width: 66ch; font-size: 15px !important; }}
#masthead ol {{
  display: flex; flex-wrap: wrap; gap: 10px 26px; margin: 16px 0 0; padding: 14px 0 0;
  border-top: 1px solid #3d2c48; list-style: none; color: #ded3e6 !important; font-size: 14px !important;
}}
#masthead ol li {{ color: #ded3e6 !important; }}
#masthead ol b {{ color: {LIME} !important; font-weight: 700 !important; margin-right: 6px; }}

#connectbar {{
  background: {TAN}; border: 1px solid {BORDER}; border-radius: 14px;
  padding: 12px 14px; flex-wrap: wrap !important;
}}
/* The sign-in HTML block ignores Gradio's scale; pin it so an empty grower
   cannot push Sign out and the status to the middle of the bar. */
#connectbar > :first-child {{ flex: 0 0 auto !important; }}
#polli-status {{
  color: {MUTED} !important; font-size: 15px !important; padding: 0 6px !important;
  white-space: nowrap; min-width: 140px; align-self: center; text-align: left;
}}
#polli-signin {{
  display: inline-block; background: {LIME}; color: {INK}; border: 1px solid {INK};
  border-radius: 999px; font-weight: 700; font-size: 15px; padding: 10px 24px;
  cursor: pointer; font-family: inherit; white-space: nowrap; align-self: center;
  text-decoration: none !important;
}}
#polli-signin:hover {{ background: {INK}; color: {LIME} !important; }}
#polli-signin:focus-visible {{ outline: 2px solid {INK}; outline-offset: 2px; }}
#polli-status b {{ color: {INK} !important; }}

#polli-devecode {{
  background: {INK}; color: {WHITE}; border-radius: 14px; padding: 16px 18px;
}}
#polli-devecode .dev-head {{ color: #ded3e6 !important; font-size: 14px; }}
#polli-devecode a {{ color: {LIME} !important; font-weight: 600; }}
#polli-usercode {{
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 28px;
  font-weight: 700; letter-spacing: 3px; color: {LIME} !important; margin: 10px 0 4px;
}}
#polli-devecode .dev-actions {{ display: flex; gap: 10px; margin: 10px 0 8px; }}
#polli-devecode button {{
  background: {LIME}; color: {INK}; border: 1px solid {LIME}; border-radius: 999px;
  font-weight: 700; font-size: 14px; padding: 8px 18px; cursor: pointer; font-family: inherit;
}}
#polli-devecode button:hover {{ background: {WHITE}; border-color: {WHITE}; }}
#polli-devecode #polli-cancel {{
  background: transparent !important; color: {WHITE} !important; border: 1px solid #5a4a66 !important;
}}
#polli-devecode #polli-cancel:hover {{ border-color: {WHITE} !important; }}
#polli-devstatus {{ color: #ded3e6 !important; font-size: 13px; }}

#polli-notice {{
  border: 1px solid {INK}; border-radius: 12px; padding: 10px 14px;
  background: {LIME}; color: {INK} !important; font-weight: 600;
}}

#pane-image, #pane-audio {{ border: 1px solid {BORDER}; border-radius: 14px; overflow: hidden; }}
#pane-image {{ background: {LAVENDER}; }}
#pane-audio {{ background: {MINT}; min-height: 380px; }}
#pane-caption {{ background: {PERIWINKLE}; border: 1px solid {BORDER}; border-radius: 14px; }}
#pane-image label, #pane-audio label, #pane-caption label {{ color: {INK} !important; font-weight: 600 !important; }}

#starterrow button {{ border-radius: 999px !important; font-size: 13px !important; }}

.footnote {{
  font-size: 13px; color: {MUTED}; margin-top: 10px; overflow-wrap: anywhere;
}}
.footnote code {{ color: {INK}; font-weight: 600; }}

#keywrap {{ display: none !important; }}

#budgethint {{
  align-self: center; padding-left: 8px; font-size: 14px; color: {SUBTLE}; line-height: 1.45;
}}
@media (max-width: 700px) {{ #budgethint {{ display: none !important; }} }}

button.primary {{
  background: {LIME} !important; color: {INK} !important; border: 1px solid {INK} !important;
  border-radius: 999px !important; font-weight: 700 !important;
}}
button.primary:hover {{ background: {INK} !important; color: {LIME} !important; }}
button.secondary {{ border-radius: 999px !important; border: 1px solid {BORDER} !important; }}

input, textarea {{ background: {WHITE} !important; color: {INK} !important; border-radius: 10px !important; }}

#foot {{
  border-top: 1px solid {BORDER}; padding-top: 16px; margin-top: 6px;
  color: {MUTED} !important; font-size: 13px !important;
}}
#foot a, #foot code {{ color: {INK} !important; font-weight: 600; }}

.gradio-container details {{ background: {TAN} !important; border: 1px solid {BORDER} !important; border-radius: 12px !important; }}
.gradio-container summary {{ color: {INK} !important; font-weight: 600 !important; }}

@media (prefers-reduced-motion: reduce) {{
  * {{ transition: none !important; animation: none !important; }}
}}
"""


def build_theme() -> gr.Theme:
    """Warm Pollinations light theme. Every dark twin is pinned to the same value,
    so OS dark mode can never put light text on a light surface."""
    theme = gr.themes.Soft(primary_hue="yellow", secondary_hue="violet", neutral_hue="stone")
    warm = {
        "body_background_fill": CREAM,
        "background_fill_primary": CREAM,
        "background_fill_secondary": CREAM,
        "block_background_fill": CREAM,
        "panel_background_fill": CREAM,
        "block_border_color": BORDER,
        "panel_border_color": BORDER,
        "body_text_color": INK,
        "body_text_color_subdued": MUTED,
        "link_text_color": INK,
        "input_background_fill": WHITE,
        "input_background_fill_hover": WHITE,
        "input_background_fill_focus": WHITE,
        "input_border_color": BORDER,
        "input_border_color_focus": INK,
        "input_placeholder_color": SUBTLE,
        "color_accent": INK,
        "block_label_text_color": MUTED,
        "block_title_text_color": INK,
        "section_header_text_color": INK,
        "button_primary_background_fill": LIME,
        "button_primary_background_fill_hover": "#D8E24F",
        "button_primary_text_color": INK,
        "button_secondary_background_fill": TAN,
        "button_secondary_background_fill_hover": BORDER,
        "button_secondary_text_color": INK,
        "block_label_background_fill": CREAM,
        "block_label_text_color": MUTED,
        "block_title_background_fill": CREAM,
        "error_text_color": INK,
        "error_border_color": INK,
        "table_odd_background_fill": CREAM,
        "table_even_background_fill": TAN,
        "code_background_fill": TAN,
    }
    # Only pass keys this theme actually exposes, then mirror each into its dark twin.
    valid = theme.__dict__
    flat = {k: v for k, v in warm.items() if k in valid}
    both = {**flat, **{f"{k}_dark": v for k, v in flat.items() if f"{k}_dark" in valid}}
    return theme.set(**both)


def build_demo() -> gr.Blocks:
    models = catalog()
    with gr.Blocks(title="Pollinations Postcard Starter", fill_width=True) as demo:
        key_state = gr.State("")
        # Hidden key bridge: it must stay in the DOM (visible=False would drop
        # it) so the browser JS can mirror the visitor key into it.
        with gr.Column(elem_id="keywrap"):
            key_bridge = gr.Textbox(
                visible=True, container=False, show_label=False,
                elem_id="polli-key-bridge", value="",
            )

        gr.HTML(
            f"""
<div id="masthead">
  <h1><span class="mark">🌼</span> Pollinations Postcard Starter</h1>
  <p>One prompt makes an image, a written caption, and spoken narration. Visitors sign in with their own
  Pollen, so nobody else's balance pays for your postcard.</p>
  <ol>
    <li><b>1</b>Sign in with your Pollen</li>
    <li><b>2</b>Describe a postcard</li>
    <li><b>3</b>Generate, then duplicate this Space to make it yours</li>
  </ol>
</div>
""",
            elem_id="masthead",
        )

        with gr.Row(elem_id="connectbar"):
            # Plain anchor, not a Gradio button: APP_JS binds clicks at the
            # document level, which survives remounts inside HF's iframe.
            signin = gr.HTML(SIGNIN_HTML)
            out_btn = gr.Button("Sign out", variant="secondary", scale=0, min_width=110, visible=False)
            status = gr.Markdown("Not connected", elem_id="polli-status", scale=1)

        gr.HTML(
            """
<div id="polli-devecode" style="display:none">
  <div class="dev-head">Open <a id='polli-devlink' href='https://enter.pollinations.ai/device'
    target='_blank' rel='noreferrer'>enter.pollinations.ai/device</a> in a new tab and approve this code:</div>
  <div id="polli-usercode">----</div>
  <div class="dev-actions">
    <button id="polli-copycode" type="button">Copy code</button>
    <button id="polli-cancel" type="button">Cancel</button>
  </div>
  <div id="polli-devstatus">This page updates by itself once you approve.</div>
</div>
"""
        )

        notice = gr.Markdown(visible=False, elem_id="polli-notice")

        prompt = gr.Textbox(
            label="Describe your postcard",
            placeholder="A lighthouse at dawn, gulls, soft fog",
            lines=3,
            max_lines=6,
            elem_id="prompt",
        )

        with gr.Row(elem_id="starterrow"):
            for s in STARTERS:
                gr.Button(s, variant="secondary", scale=1).click(
                    lambda x=s: x, outputs=[prompt], api_visibility="private"
                )

        with gr.Accordion("Model and voice options", open=False):
            with gr.Row():
                image_model = gr.Dropdown(models["image"], value=models["image"][0], label="Image model")
                text_model = gr.Dropdown(models["text"], value=models["text"][0], label="Text model")
                voice = gr.Dropdown(FALLBACK_VOICES, value="nova", label="Narration voice")

        with gr.Row():
            gen_btn = gr.Button("Generate postcard", variant="primary", scale=0, min_width=210)
            gr.HTML(
                "<div id='budgethint'>You approve a Pollen budget on the Pollinations page. "
                "Nothing runs without your key.</div>"
            )

        with gr.Row(equal_height=True):
            with gr.Column(elem_id="pane-image", scale=5):
                out_image = gr.Image(
                    label="Image",
                    height=380,
                    type="filepath",
                    interactive=False,
                )
            with gr.Column(elem_id="pane-audio", scale=4):
                out_audio = gr.Audio(label="Narration", type="filepath", interactive=False)

        with gr.Column(elem_id="pane-caption"):
            out_caption = gr.Textbox(
                label="Caption and transcript",
                placeholder="The caption writes itself from your prompt, then gets read aloud.",
                lines=3,
                max_lines=6,
                interactive=False,
            )

        with gr.Accordion("Make it your own", open=False):
            gr.Markdown(
                """
1. **Duplicate this Space.** CPU Basic is enough; this app calls the Pollinations API and runs no model.
2. **Optional App Key.** Create one at enter.pollinations.ai/keys and set it as Space Variable
   `POLLINATIONS_APP_KEY`. Variables carry over on Duplicate, Secrets do not. No Redirect URI is
   needed: sign-in uses the device flow, users approve a short code. Without a key, approval still
   works and simply shows the Space hostname instead of your app name.
3. **Swap the prompts or defaults.** Starter prompts sit in `STARTERS`, default models in `FALLBACK_IMAGE`
   and `FALLBACK_TEXT`, voices in `FALLBACK_VOICES`.
4. **Model IDs are `publisher/model` names.** Old aliases still resolve, and both are accepted in the dropdown.
5. **Submit your app** with the app submission form, Quest field `#17249`.
"""
            )

        gr.HTML(
            "<div id='foot'>Agents: add this Space from your Hub MCP settings, then call the "
            "<code>make_postcard</code> tool. It needs your Pollen key as an argument. "
            "<a href='https://enter.pollinations.ai/keys' target='_blank' rel='noreferrer'>Manage Pollen keys</a>"
            "<div class='footnote'>Sign-in uses the Pollinations device flow, so your App Key "
            "needs no Redirect URI.</div>"
            "</div>"
        )

        def _set_key(k):
            key = (k or "").strip()
            name, line = check_key(key)
            # The status line is the feedback; the notice bar stays reserved for
            # generation errors. The sign-in link leaves once a key exists.
            return (key, line, gr.update(visible=False), gr.update(visible=key != ""),
                    gr.update(value="" if key else SIGNIN_HTML))

        gen_btn.click(
            ui_generate,
            inputs=[prompt, key_bridge, image_model, text_model, voice],
            outputs=[out_image, out_audio, out_caption, prompt, notice],
            api_visibility="private",
        )
        prompt.submit(
            ui_generate,
            inputs=[prompt, key_bridge, image_model, text_model, voice],
            outputs=[out_image, out_audio, out_caption, prompt, notice],
            api_visibility="private",
        )
        key_bridge.change(_set_key, inputs=[key_bridge], outputs=[key_state, status, notice, out_btn, signin],
                         show_progress="minimal", api_visibility="private")
        out_btn.click(sign_out, inputs=[key_state], outputs=[key_bridge, key_state, status],
                      js="() => { if (window.polliDisconnect) { window.polliDisconnect(); } }", api_visibility="private")

        # Pure-logic MCP tool: one postcard endpoint for agents.
        gr.api(make_postcard, api_name="make_postcard")
    return demo


demo = build_demo()

if __name__ == "__main__":
    demo.launch(
        mcp_server=True,
        theme=build_theme(),
        css=APP_CSS,
        # head, not js: runs during HTML parse, so the window helpers and the
        # delegated click handler exist before Gradio mounts anything.
        head=f"<script>{APP_JS}</script>",
        show_error=True,
    )