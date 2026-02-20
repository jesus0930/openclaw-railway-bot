import logging
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

try:
    import google.generativeai as genai
except Exception:
    genai = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def get_gemini_reply(text: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or genai is None:
        return "봇은 살아있습니다. (GEMINI_API_KEY 미설정 또는 라이브러리 없음)"

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    resp = model.generate_content(text)
    return (getattr(resp, "text", "") or "응답이 비어 있습니다.").strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("안녕하세요! 봇이 정상 실행 중입니다 ✅")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text or ""
    try:
        reply = get_gemini_reply(user_text)
    except Exception as e:
        logger.exception("gemini error")
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

    logger.info("Bot starting polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
