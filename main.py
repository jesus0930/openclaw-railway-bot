import json
import logging
import os
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OWNER_IDS = {
    s.strip()
    for s in os.getenv("ALLOW_FROM", os.getenv("OWNER_IDS", "5623991355")).split(",")
    if s.strip()
}

ENABLE_LLM_FALLBACK = os.getenv("ENABLE_LLM_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}

# Bridge: bot -> this service /bridge -> upstream OpenClaw endpoint
PORT = int(os.getenv("PORT", "8080"))
RAILWAY_PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
SELF_BASE_URL = f"https://{RAILWAY_PUBLIC_DOMAIN}" if RAILWAY_PUBLIC_DOMAIN else ""

OPENCLAW_WEBHOOK_TOKEN = os.getenv("OPENCLAW_WEBHOOK_TOKEN", "").strip()
OPENCLAW_WEBHOOK_URL = os.getenv("OPENCLAW_WEBHOOK_URL", "").strip() or (
    f"{SELF_BASE_URL}/bridge" if SELF_BASE_URL else ""
)
OPENCLAW_UPSTREAM_URL = os.getenv("OPENCLAW_UPSTREAM_URL", "").strip()
OPENCLAW_UPSTREAM_TOKEN = os.getenv("OPENCLAW_UPSTREAM_TOKEN", "").strip()
OPENCLAW_UPSTREAM_MODE = os.getenv("OPENCLAW_UPSTREAM_MODE", "webhook").strip().lower()  # webhook|chat

LLM_API_URL = os.getenv(
    "LLM_API_URL",
    os.getenv("LLM_ROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"),
)
LLM_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o")


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = Request(url, data=data, headers=req_headers, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except Exception:
            return {"text": body}


def _upstream_headers() -> dict:
    headers = {}
    if OPENCLAW_UPSTREAM_TOKEN:
        # keep bearer for backward compatibility
        headers["Authorization"] = f"Bearer {OPENCLAW_UPSTREAM_TOKEN}"
        # common gateway header variants
        headers["X-OpenClaw-Token"] = OPENCLAW_UPSTREAM_TOKEN
        headers["X-Gateway-Token"] = OPENCLAW_UPSTREAM_TOKEN
    return headers


def _chat_completions_url(base_or_full: str) -> str:
    u = base_or_full.rstrip("/")
    # supports OpenAI-compatible full path and simple proxy path (/chat)
    if u.endswith("/v1/chat/completions") or u.endswith("/chat"):
        return u
    return u + "/v1/chat/completions"


def call_openclaw_bridge(text: str, user_id: str, chat_id: str) -> str:
    if not OPENCLAW_WEBHOOK_URL:
        return ""
    headers = {}
    if OPENCLAW_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_WEBHOOK_TOKEN}"

    payload = {"text": text, "user_id": user_id, "chat_id": chat_id, "source": "telegram-railway-bot"}
    try:
        data = _post_json(OPENCLAW_WEBHOOK_URL, payload, headers=headers, timeout=90)
        return str(data.get("reply") or data.get("text") or data.get("message") or "").strip()
    except (HTTPError, URLError, OSError):
        logger.exception("OpenClaw bridge error")
        return ""


def call_upstream_direct(text: str) -> str:
    if not OPENCLAW_UPSTREAM_URL:
        return ""
    url = _chat_completions_url(OPENCLAW_UPSTREAM_URL)
    body = {"model": LLM_MODEL, "messages": [{"role": "user", "content": text}], "max_tokens": 2048}
    headers = _upstream_headers()
    try:
        data = _post_json(url, body, headers=headers, timeout=90)
        return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
    except (HTTPError, URLError, OSError):
        logger.exception("upstream direct error")
        return ""


def get_llm_reply(text: str) -> str:
    body = {"model": LLM_MODEL, "messages": [{"role": "user", "content": text}], "max_tokens": 1024}
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    try:
        data = _post_json(LLM_API_URL, body, headers=headers, timeout=60)
        return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "응답이 비어 있습니다.").strip()
    except (HTTPError, URLError, OSError):
        logger.exception("LLM API error")
        return "LLM 요청 오류"


def is_allowed(update: Update) -> bool:
    uid = str(update.effective_user.id) if update.effective_user else ""
    return uid in OWNER_IDS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("권한이 없습니다.")
        return
    if OPENCLAW_WEBHOOK_URL:
        mode = "openclaw-bridge"
    elif OPENCLAW_UPSTREAM_URL:
        mode = "upstream-direct"
    elif ENABLE_LLM_FALLBACK:
        mode = "llm-fallback"
    else:
        mode = "no-backend"
    await update.message.reply_text(f"봇 정상 실행 ✅ (mode={mode})")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("권한이 없습니다.")
        return
    mode = "bridge" if OPENCLAW_WEBHOOK_URL else ("upstream-direct" if OPENCLAW_UPSTREAM_URL else "fallback")
    await update.message.reply_text(
        f"mode={mode}\n"
        f"bridge={'on' if OPENCLAW_WEBHOOK_URL else 'off'}\n"
        f"upstream={'on' if OPENCLAW_UPSTREAM_URL else 'off'}\n"
        f"upstream_mode={OPENCLAW_UPSTREAM_MODE}\n"
        f"upstream_token={'set' if OPENCLAW_UPSTREAM_TOKEN else 'missing'}\n"
        f"llm_fallback={ENABLE_LLM_FALLBACK}"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("권한이 없습니다.")
        return

    text = (update.message.text or "").strip()
    uid = str(update.effective_user.id) if update.effective_user else ""
    cid = str(update.effective_chat.id) if update.effective_chat else ""

    reply = call_openclaw_bridge(text, uid, cid)
    if not reply:
        reply = call_upstream_direct(text)
    if not reply:
        reply = get_llm_reply(text) if ENABLE_LLM_FALLBACK else "upstream 연결 실패"

    await update.message.reply_text(reply[:3500])


app_http = Flask(__name__)


@app_http.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app_http.post("/bridge")
def bridge():
    # Require bearer token
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {OPENCLAW_WEBHOOK_TOKEN}" if OPENCLAW_WEBHOOK_TOKEN else None
    if expected and auth != expected:
        return jsonify({"error": "unauthorized"}), 401

    if not OPENCLAW_UPSTREAM_URL:
        return jsonify({"reply": "upstream_not_configured"}), 503

    payload = request.get_json(silent=True) or {}
    headers = _upstream_headers()

    try:
        if OPENCLAW_UPSTREAM_MODE == "chat":
            text = str(payload.get("text") or "").strip()
            body = {"model": LLM_MODEL, "messages": [{"role": "user", "content": text}], "max_tokens": 2048}
            data = _post_json(_chat_completions_url(OPENCLAW_UPSTREAM_URL), body, headers=headers, timeout=90)
            content = (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
            return jsonify({"reply": content})

        data = _post_json(OPENCLAW_UPSTREAM_URL, payload, headers=headers, timeout=90)
        return jsonify(data)
    except Exception:
        logger.exception("bridge upstream error")
        return jsonify({"reply": "upstream_error"}), 502


def run_http() -> None:
    app_http.run(host="0.0.0.0", port=PORT)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    threading.Thread(target=run_http, daemon=True).start()

    tg = Application.builder().token(token).build()
    tg.add_handler(CommandHandler("start", start))
    tg.add_handler(CommandHandler("status", status))
    tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("bot start bridge=%s upstream=%s", bool(OPENCLAW_WEBHOOK_URL), bool(OPENCLAW_UPSTREAM_URL))
    tg.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
