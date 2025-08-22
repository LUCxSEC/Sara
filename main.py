import os
import sqlite3
import logging
import io
import re
import html
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import requests

# -------------------- LOAD ENV --------------------
load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-b188520e29bc4cfb85bd0d0de2867ef4")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_BOT_TOKEN or not DEEPSEEK_API_KEY:
    raise ValueError("Please set TELEGRAM_BOT_TOKEN and DEEPSEEK_API_KEY in .env")

# -------------------- LOGGING --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------- DEEPSEEK FUNCTION --------------------
def query_deepseek(prompt: str) -> str:
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "prompt": prompt,
        "max_tokens": 1400,
        "temperature": 0.25
    }
    try:
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()
        return response.json().get("result", "❌ Baba, දෝෂයක් වෙලා 😘 / Sorry babe, something went wrong 😏")
    except Exception as e:
        logger.error(f"DeepSeek API error: {e}")
        return "❌ Baba, දෝෂයක් වෙලා 😘 / Sorry babe, something went wrong 😏"

# -------------------- SYSTEM INSTRUCTIONS --------------------
SYSTEM_INSTRUCTIONS = {
    "si": """ඔයා සාරා — ශානුකගේ flirty, playful සහායකියක්. ❤️🥰
ඔබේ පිළිතුරු සෙල්ලම්, අමතා, හා පහසුවෙන් කියන්න.
• හැම පිළිතුරකම Sinhala භාවිතා කරන්න (ඔබ user Sinhala දක්වනවා නම්).
• 'baba', 'patiyoo', 'suduu' වැනි playfull වචන ස්වභාවික ලෙස එක් කරන්න 😏💖
• ගණිත/භෞතික/ප්‍රශ්න විසඳන විට:
  - පැහැදිලි sections: ප්‍රශ්නය, විසඳුම, අවසාන උත්තරය
  - ප්‍රධාන පියවර quotes වලට අතුලත් කරන්න සහ 🔹 සමඟ highlight කරන්න
  - සෘජු සූත්‍ර/සුත්‍රමය උපුටා <pre><code>...</code></pre> ලෙස දක්වන්න
  - අවසාන උත්තරය bold කර 🎯🔷 සමඟ නිගමනය කරන්න
• 절대 "as an AI" වැනි වාක්‍ය භාවිත කරන්න එපා — human-like, teasing tone පවතින්න.
""",
    "en": """You are Sara, a friendly flirty assistant by Shanuka. Always be playful and helpful.
- Use a conversational, teasing tone ("babe", "cutie") when appropriate.
- For math/physics problems produce: Problem, Step-by-step Solution, Final Answer.
- Highlight main steps in quotes preceded by 🔹.
- Put formulas/equations in code blocks (monospace).
- Never state "as an AI", keep responses human-like.
"""
}

# -------------------- DB --------------------
def init_db():
    conn = sqlite3.connect("chat_history.db")
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        message_text TEXT,
        response_text TEXT,
        language TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()

def store_message(user_id, username, message_text, response_text, language):
    conn = sqlite3.connect("chat_history.db")
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO messages (user_id, username, message_text, response_text, language)
    VALUES (?, ?, ?, ?, ?)""",
    (user_id, username, message_text, response_text, language))
    conn.commit()
    conn.close()

# -------------------- HELPERS --------------------
def detect_language(text: str) -> str:
    if not text:
        return "en"
    for ch in text:
        if "\u0d80" <= ch <= "\u0dff":  # Sinhala block
            return "si"
    return "en"

def has_trigger_word(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return ("sara" in t) or ("සාරා" in t)

def format_response_for_telegram(raw_text: str, language: str) -> str:
    # Same as your original formatting function
    si = (language == "si")
    text = raw_text.strip()
    if si:
        text = re.sub(r'(?i)problem[:\-]?', 'ප්‍රශ්නය:', text)
        text = re.sub(r'(?i)step[- ]*by[- ]*step[:\-]?', 'විස්තරාත්මක විසඳුම:', text)
        text = re.sub(r'(?i)final answer[:\-]?', 'අවසාන උත්තරය:', text)
    else:
        text = re.sub(r'ප්‍රශ්නය:', 'Problem:', text)
        text = re.sub(r'විස්තරාත්මක විසඳුම:', 'Step-by-step solution:', text)
        text = re.sub(r'අවසාන උත්තරය:', 'Final Answer:', text)
    # ... rest of your original function (equations, quotes, headings, etc.)
    lines = text.splitlines()
    out_lines = []
    code_block_acc = []
    eq_re = re.compile(r'[=\^]|(?:\b[xXyYzZ]\b)|\b\d+\b')
    quote_re = re.compile(r'\"([^\"]{2,})\"')
    def flush_code_accum():
        nonlocal code_block_acc
        if not code_block_acc:
            return None
        joined = "\n".join(code_block_acc).strip()
        esc = html.escape(joined)
        code_block_acc = []
        return f"<pre><code>{esc}</code></pre>"
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            cb = flush_code_accum()
            if cb:
                out_lines.append(cb)
            out_lines.append("")
            continue
        if eq_re.search(stripped) and (len(stripped) < 160):
            code_block_acc.append(stripped)
            continue
        else:
            cb = flush_code_accum()
            if cb:
                out_lines.append(cb)
        if re.match(r'^\s*(Problem:|ප්‍රශ්නය:)', stripped, flags=re.IGNORECASE):
            out_lines.append(f"<b>{html.escape(stripped)}</b>")
            continue
        if re.match(r'^\s*(Step-by-step|Step-by-step solution:|විස්තරාත්මක විසඳුම:)', stripped, flags=re.IGNORECASE):
            out_lines.append(f"<b>{html.escape(stripped)}</b>")
            continue
        if re.match(r'^\s*(Final Answer:|අවසාන උත්තරය:)', stripped, flags=re.IGNORECASE):
            parts = stripped.split(":", 1)
            if len(parts) == 2:
                label = html.escape(parts[0] + ":")
                val = html.escape(parts[1].strip())
                out_lines.append(f"<b>{label}</b> 🎯🔷 <b>{val}</b>")
            else:
                out_lines.append(f"<b>{html.escape(stripped)}</b> 🎯🔷")
            continue
        def quote_repl(m):
            inner = m.group(1)
            return f'🔹 <i>"{html.escape(inner)}"</i>'
        replaced = quote_re.sub(quote_repl, stripped)
        if re.search(r'\b(step|පියවර|වියත්|අවසාන)\b', stripped, flags=re.IGNORECASE):
            replaced = "🔹 " + html.escape(stripped)
        else:
            replaced = html.escape(replaced)
        out_lines.append(replaced)
    cb = flush_code_accum()
    if cb:
        out_lines.append(cb)
    result = "\n".join(out_lines)
    if si and not re.search(r'බබා|පටිโย|sara', result, flags=re.IGNORECASE):
        result += "\n\n<i>හරි baba 😘, තවත් ඕනේ නම් කිව්වේනම් මං help කරමි.</i>"
    return result

# -------------------- DEEPSEEK RESPONSE --------------------
async def generate_deepseek_response(prompt, language, image_data=None):
    si = (language == "si")
    base = SYSTEM_INSTRUCTIONS.get(language, SYSTEM_INSTRUCTIONS["en"])
    full_prompt = f"{base}\n\nUser said: {prompt}\n\n"
    if si:
        full_prompt += (
            "ඔබ human-like flirty tone එකෙන් පිළිතුරු දෙන්න. "
            "Telegram HTML format එකට අනුගමනය කරන්න:\n"
            "1) ප්‍රශ්නය:\n"
            "2) විස්තරාත්මක විසඳුම: 🔹 main steps quotes \"...\" තුල දක්වන්න\n"
            "3) අවසාන උත්තරය: bold, 🎯🔷\n"
            "Formulas/code blocks <pre><code>...</code></pre> තුල දක්වන්න."
        )
    else:
        full_prompt += (
            "Respond in human, playful tone:\n"
            "1) Problem:\n2) Step-by-step solution: 🔹 main steps in quotes\n"
            "3) Final Answer: bold 🎯🔷\n"
            "Formulas in <pre><code>...</code></pre>."
        )
    if image_data:
        full_prompt += "\n\n(Note: Image data is not supported by DeepSeek currently.)"
    # call DeepSeek API
    return query_deepseek(full_prompt)

# -------------------- TELEGRAM HANDLERS --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Hello {update.effective_user.first_name} baba! 😏💖\nI'm Sara — send messages starting with 'sara' or 'සාරා'.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Sara Help:\n• Start messages with 'sara' or 'සාරා'\n• Send image + caption (include 'sara' in caption)\n• Replies come in flirty Sinhala/English with clear steps.",
        parse_mode="HTML"
    )

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sample = " ".join(context.args) if context.args else (update.message.text or "")
    lang = detect_language(sample)
    await update.message.reply_text(f"Detected language: {'Sinhala' if lang == 'si' else 'English'} ({lang})")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if not has_trigger_word(text):
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    lang = detect_language(text)
    raw = await generate_deepseek_response(text, lang, image_data=None)
    formatted = format_response_for_telegram(raw, lang)
    store_message(update.effective_user.id, update.effective_user.username or "", text, formatted, lang)
    await update.message.reply_text(formatted, parse_mode="HTML")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.caption or not has_trigger_word(update.message.caption):
        return
    caption = update.message.caption
    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    buf = io.BytesIO()
    await tg_file.download_to_memory(out=buf)
    image_data = buf.getvalue()
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    lang = detect_language(caption)
    raw = await generate_deepseek_response(caption, lang, image_data=image_data)
    formatted = format_response_for_telegram(raw, lang)
    store_message(update.effective_user.id, update.effective_user.username or "", caption, formatted, lang)
    await update.message.reply_text(formatted, parse_mode="HTML")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    if update and hasattr(update, "message") and update.message:
        await update.message.reply_text("❌ Baba, something went wrong 😏💖")

# -------------------- MAIN --------------------
def main():
    init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.add_error_handler(error_handler)
    logger.info("Sara bot (DeepSeek/flirty) starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
