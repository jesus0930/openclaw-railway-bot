import logging
import os
import json

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# OpenAI-compatible endpoint (OpenRouter on Railway, local router on dev)
LLM_API_URL = os.getenv(
    "LLM_API_URL",
    os.getenv("LLM_ROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"),
)
LLM_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o")


def get_llm_reply(text: str) -> str:
    """Send a chat completion request via OpenAI-compatible API."""
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": text}],
        "max_tokens": 1024,
    }).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    req = Request(LLM_API_URL, data=body, headers=headers, method="POST")

    try:
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            return (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                or "응답이 비어 있습니다."
            ).strip()
    except (HTTPError, URLError, OSError) as e:
        logger.exception("LLM API error")
        return f"LLM 요청 오류: {e}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("안녕하세요! 봇이 정상 실행 중입니다 ✅")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text or ""
    try:
        reply = get_llm_reply(user_text)
    except Exception as e:
        logger.exception("llm error")
        reply = f"오류가 발생했습니다: {e}"
    await update.message.reply_text(reply[:3500])


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("ok")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    logger.info("Bot starting polling (API=%s, model=%s)...", LLM_API_URL, LLM_MODEL)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
