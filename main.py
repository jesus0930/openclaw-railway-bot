"""
Telegram Echo Bot with Gemini API integration and robust error handling.

This script implements a simple Telegram bot that echoes the user’s
messages using a Gemini model. It tries to use a preferred model
(`PREFERRED_MODEL`), but will dynamically fall back to the first
available model if the preferred one is unavailable. Errors from the
Gemini API are caught and logged, and the bot notifies the user
instead of silently failing.

Environment variables:

* ``TELEGRAM_BOT_TOKEN`` – Bot token obtained from BotFather.
* ``GEMINI_API_KEY`` – API key from Google AI Studio. Only this key is
  used; Vertex AI credentials are deliberately ignored to avoid
  accidental routing to Vertex endpoints【841821725505580†L180-L188】.

Example usage::

    $ export TELEGRAM_BOT_TOKEN="xxx"
    $ export GEMINI_API_KEY="yyy"
    $ python hub_bot.py

This will start a polling bot that replies to each received message
using a Gemini model. If the chosen model is not found (404), the
bot lists available models and falls back automatically.
"""

import logging
import os
import sys
from typing import List, Optional

try:
    # Import the official google-generative-ai library.  This library
    # defaults to the v1beta Generative Language API; some older models,
    # such as ``gemini-1.5-flash``, are retired or unavailable on v1beta
    # endpoints【126615391682631†L800-L810】.  If you see 404 errors, upgrade
    # your model selection or switch to a supported version.
    import google.generativeai as genai
except ImportError as exc:
    print("The google-generativeai library is not installed. Install it via"
          " `pip install google-generativeai` and try again.", file=sys.stderr)
    raise

try:
    from telegram import Update
    from telegram.ext import (CallbackContext, CommandHandler, Filters,
                              MessageHandler, Updater)
except ImportError as exc:
    print("The python-telegram-bot library is not installed. Install it via"
          " `pip install python-telegram-bot` and try again.", file=sys.stderr)
    raise


# Configure basic logging. Logs are printed to stdout so that they
# appear in container logs or hosting platforms.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper functions for model selection
# ---------------------------------------------------------------------------
PREFERRED_MODEL = "gemini-3-flash-preview"  # default fallback if dynamic lookup fails


def list_available_models() -> List[str]:
    """Return a list of model names available to the configured API key.

    This function calls ``genai.list_models()`` and extracts the names from
    the returned objects. It may raise exceptions if network or
    authentication fails. In that case, the exception will be propagated
    to the caller to handle.
    """
    models = []
    for m in genai.list_models():
        # Some older versions of the library return a dict-like object with
        # a ``name`` attribute; others return strings. Normalize here.
        try:
            name = m.name  # type: ignore[attr-defined]
        except AttributeError:
            name = str(m)
        models.append(name)
    return models


def select_best_model(preferred: str) -> str:
    """Return the preferred model if available, otherwise choose a sensible fallback.

    The function inspects the available models via ``list_available_models``.
    If the preferred model is present, it is returned unchanged.  Otherwise
    the first model containing 'flash' (fast inference) is chosen.  If no
    such model exists, the first available model is returned.  As a last
    resort, the original preferred model is returned.
    """
    try:
        available = list_available_models()
        logger.info("Available models: %s", available)
        if preferred in available:
            return preferred
        # choose any flash or pro model to maintain speed and quality
        for name in available:
            if "flash" in name:
                return name
        # fall back to the first available model
        if available:
            return available[0]
    except Exception:
        # If listing models fails, log and fall back to preferred
        logger.exception("Failed to list models when selecting best model")
    return preferred


# ---------------------------------------------------------------------------
# Telegram bot logic
# ---------------------------------------------------------------------------

def generate_gemini_reply(prompt: str, model_name: str) -> str:
    """Generate a response from Gemini using the specified model.

    If the model is not found (404) or another API error occurs, the
    exception is propagated to the caller for handling.  The caller
    is responsible for choosing another model if necessary.
    """
    # Build a GenerativeModel object for the model
    model = genai.GenerativeModel(model_name)
    # The input is provided as a list (single message) to enable later
    # multi-part prompts or tool calls.
    response = model.generate_content([prompt])
    # ``response.text`` contains the generated text in the python SDK
    return response.text


def echo(update: Update, context: CallbackContext) -> None:
    """Handle incoming messages: echo user message by generating via Gemini.

    On errors, log details and notify the user.  If a 404 NotFound
    occurs due to an invalid model, the bot selects a new model and
    retries once.  Any remaining exceptions are reported to the user.
    """
    user_input = update.effective_message.text or ""
    chat_id = update.effective_chat.id
    # Retrieve or initialize the model in bot_data
    model_name = context.bot_data.get("model_name")
    if not model_name:
        model_name = select_best_model(PREFERRED_MODEL)
        context.bot_data["model_name"] = model_name
        logger.info("Initial model selected: %s", model_name)
    try:
        # Attempt to generate a reply
        reply = generate_gemini_reply(user_input, model_name)
        update.effective_message.reply_text(reply)
    except genai.types.generation_types.NotFound as e:  # type: ignore[attr-defined]
        # Model not found; choose another model and retry
        logger.warning("Model %s not found, selecting fallback", model_name)
        fallback = select_best_model(PREFERRED_MODEL)
        context.bot_data["model_name"] = fallback
        try:
            reply = generate_gemini_reply(user_input, fallback)
            update.effective_message.reply_text(reply)
            update.effective_message.reply_text(
                f"⚠️ 이전 모델 '{model_name}'을 찾을 수 없어 '{fallback}'로 전환했습니다.")
        except Exception as inner:
            logger.exception("Error after switching models")
            update.effective_message.reply_text(
                "죄송합니다, 현재 AI 응답을 가져올 수 없습니다. 관리자에게 문의해주세요.")
    except Exception as e:
        # Generic error: log and inform the user
        logger.exception("Error generating reply")
        update.effective_message.reply_text(
            "⚠️ 요청을 처리하는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")


def start(update: Update, context: CallbackContext) -> None:
    """Send a welcome message when the /start command is issued."""
    update.message.reply_text(
        "안녕하세요! 이 봇은 당신의 메시지에 대한 제미나이 모델 응답을 반환합니다."
    )


def main() -> None:
    """Entry point to set up the Telegram bot and Gemini API."""
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not telegram_token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is not set.")
        sys.exit(1)
    if not api_key:
        logger.error("GEMINI_API_KEY environment variable is not set.")
        sys.exit(1)
    # Configure Gemini API key.  Only GEMINI_API_KEY is used to ensure
    # the request goes to Google AI Studio (Gemini API) and not Vertex AI【841821725505580†L180-L188】.
    genai.configure(api_key=api_key)
    # Create the Updater and pass the bot token.
    updater = Updater(token=telegram_token, use_context=True)
    dispatcher = updater.dispatcher
    # Register handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))
    # Start polling
    updater.start_polling()
    logger.info("Telegram bot started.")
    updater.idle()


if __name__ == "__main__":
    main()