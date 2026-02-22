import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Optional OpenClaw bridge webhook (recommended for remote control mode)
# If set, every inbound text is forwarded first.
OPENCLAW_WEBHOOK_URL = os.getenv("OPENCLAW_WEBHOOK_URL", "").strip()
OPENCLAW_WEBHOOK_TOKEN = os.getenv("OPENCLAW_WEBHOOK_TOKEN", "").strip()

# Fallback LLM (disabled by default for tighter security)
ENABLE_LLM_FALLBACK = os.getenv("ENABLE_LLM_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}
LLM_API_URL = os.getenv(
    "LLM_API_URL",
    os.getenv("LLM_ROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"),
)
LLM_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o")

# Security: only owner(s) can use bot
OWNER_IDS = {
    s.strip()
    for s in os.getenv("ALLOW_FROM", os.getenv("OWNER_IDS", "5623991355")).split(",")
    if s.strip()
}


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


def call_openclaw_bridge(text: str, user_id: str, chat_id: str) -> str:
    if not OPENCLAW_WEBHOOK_URL:
        return ""

    headers = {}
    if OPENCLAW_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_WEBHOOK_TOKEN}"

    payload = {
        "text": text,
        "user_id": user_id,
        "chat_id": chat_id,
        "source": "telegram-railway-bot",
    }

    try:
        data = _post_json(OPENCLAW_WEBHOOK_URL, payload, headers=headers, timeout=90)
        # Accept common response keys
        reply = (
            data.get("reply")
            or data.get("text")
            or data.get("message")
            or ""
        )
        return str(reply).strip()
    except (HTTPError, URLError, OSError) as e:
        logger.exception("OpenClaw bridge error")
        return f"OpenClaw bridge 오류: {e}"


def get_llm_reply(text: str) -> str:
    body = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": text}],
        "max_tokens": 1024,
    }
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    try:
        data = _post_json(LLM_API_URL, body, headers=headers, timeout=60)
        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            or "응답이 비어 있습니다."
        ).strip()
    except (HTTPError, URLError, OSError) as e:
        logger.exception("LLM API error")
        return f"LLM 요청 오류: {e}"


def is_allowed(update: Update) -> bool:
    uid = str(update.effective_user.id) if update.effective_user else ""
    return uid in OWNER_IDS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("권한이 없습니다.")
        return
    if OPENCLAW_WEBHOOK_URL:
        mode = "openclaw-bridge"
    else:
        mode = "llm-fallback" if ENABLE_LLM_FALLBACK else "bridge-required"
    await update.message.reply_text(f"봇 정상 실행 ✅ (mode={mode})")


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("권한이 없습니다.")
        return
    await update.message.reply_text("pong")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("권한이 없습니다.")
        return
    token_source = "env" if os.getenv("TELEGRAM_BOT_TOKEN", "").strip() else "none"
    running = True
    await update.message.reply_text(
        f"running={running}\n"
        f"tokenSource={token_source}\n"
        f"bridge={'on' if OPENCLAW_WEBHOOK_URL else 'off'}\n"
        f"owners={','.join(sorted(OWNER_IDS))}"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("권한이 없습니다.")
        return

    text = (update.message.text or "").strip()
    uid = str(update.effective_user.id) if update.effective_user else ""
    cid = str(update.effective_chat.id) if update.effective_chat else ""

    reply = ""
    # 1) Try OpenClaw bridge first
    if OPENCLAW_WEBHOOK_URL:
        reply = call_openclaw_bridge(text, uid, cid)

    # 2) Optional fallback to LLM
    if not reply:
        if ENABLE_LLM_FALLBACK:
            reply = get_llm_reply(text)
        else:
            reply = "보안모드: 브릿지 미설정/응답없음. 관리자에게 OPENCLAW_WEBHOOK_URL 설정을 요청하세요."

    await update.message.reply_text(reply[:3500])


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info(
        "Bot starting polling (bridge=%s, llm_api=%s, owners=%s)",
        bool(OPENCLAW_WEBHOOK_URL),
        LLM_API_URL,
        sorted(OWNER_IDS),
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
