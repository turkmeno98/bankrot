import os
import sqlite3
import requests
import re
import json
import logging
from telebot import TeleBot
from telebot.types import InlineQueryResultArticle, InputTextMessageContent, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import telebot

logging.basicConfig(level=logging.INFO)
bot = TeleBot(os.getenv('BOT_TOKEN'))
BASE_URL = "https://fedresurs.ru"

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,*/*;q=0.8',
    'Referer': 'https://fedresurs.ru/',
    'X-Requested-With': 'XMLHttpRequest'
})

def init_db():
    conn = sqlite3.connect('inns.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_inns 
                 (user_id INTEGER, inn TEXT, created_at TEXT, PRIMARY KEY (user_id, inn))''')
    conn.commit()
    conn.close()

def add_inn(user_id, inn):
    conn = sqlite3.connect('inns.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO user_inns (user_id, inn, created_at) VALUES (?, ?, ?)",
              (user_id, inn, telebot.util.formatted_datetime()))
    conn.commit()
    conn.close()

def get_user_inns(user_id):
    conn = sqlite3.connect('inns.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT inn FROM user_inns WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    inns = [row[0] for row in c.fetchall()]
    conn.close()
    return inns

def parse_bankrot(inn):
    try:
        # Поиск
        params = {'query': inn, 'searchType': 'all', 'inn': inn}
        resp = session.get(f"{BASE_URL}/search-results", params=params)
        guid_match = re.search(r'/company/([a-f0-9\-]+)|/person/([a-f0-9\-]+)', resp.text)
        if not guid_match:
            return f"❌ ИНН {inn}: не найден"
        
        guid = guid_match.group(1) or guid_match.group(2)
        entity_type = 'company' if guid_match.group(1) else 'person'
        
        # Публикации JSON
        json_url = f"{BASE_URL}/backend/{entity_type}s/{guid}/publications"
        params = {'limit': 10, 'offset': 0}
        session.headers['Referer'] = f"{BASE_URL}/{entity_type}/{guid}"
        resp_json = session.get(json_url, params=params)
        data = resp_json.json()
        
        if not data.get('pageData'):
            return f"❌ ИНН {inn}: публикаций нет"
        
        result = f"✅ *{len(data['pageData'])}* банкротств (ИНН `{inn}`)\n\n"
        for item in data['pageData'][:10]:
            number = item.get('number', 'N/A')
            type_ = item.get('type', 'N/A')[:20]
            date = item.get('datePublish', 'N/A')[:10]
            result += f"• {number} | {type_} | {date}\n"
        return result[:4000]
    except:
        return f"💥 Ошибка поиска {inn}"

init_db()

@bot.message_handler(commands=['start'])
def start(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton('/add_inn'), KeyboardButton('/my_inns'))
    bot.send_message(message.chat.id, 
        "🔍 Inline Fedresurs Parser\n\n"
        "1️⃣ `/add_inn 7707083893` — сохранить ИНН\n"
        "2️⃣ `@yourbot 7707083893` — inline поиск\n"
        "3️⃣ `/my_inns` — твои ИНН\n\n"
        "💡 Пиши ИНН боту в любом чате!", reply_markup=markup)

@bot.message_handler(commands=['add_inn'])
def add_inn_cmd(message):
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "❌ /add_inn 7707083893")
    inn = parts[1]
    add_inn(message.from_user.id, inn)
    bot.reply_to(message, f"✅ ИНН `{inn}` сохранен")

@bot.message_handler(commands=['my_inns'])
def my_inns(message):
    inns = get_user_inns(message.from_user.id)
    if not inns:
        return bot.reply_to(message, "📝 ИНН не сохранены. /add_inn 7707083893")
    
    markup = InlineKeyboardMarkup(row_width=1)
    for inn in inns[:10]:
        markup.add(InlineKeyboardButton(f"🔍 {inn}", callback_data=f"search:{inn}"))
    
    text = f"📋 Твои ИНН ({len(inns)} шт.):\n\n" + "\n".join([f"• `{inn}`" for inn in inns[:5]])
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('search:'))
def search_callback(call):
    inn = call.data.split(':', 1)[1]
    bot.answer_callback_query(call.id, "🔍 Ищем...")
    result = parse_bankrot(inn)
    bot.edit_message_text(result, call.message.chat.id, call.message.message_id, parse_mode='Markdown')

@bot.inline_handler(lambda query: query.query and query.query.isdigit())
def inline_bankrot(query):
    inn = query.query.strip()
    result = parse_bankrot(inn)
    
    r = InlineQueryResultArticle(
        id=inn,
        title=f"Банкротства ИНН {inn}",
        description=result[:100],
        input_message_content=InputTextMessageContent(result, parse_mode='Markdown')
    )
    bot.answer_inline_query(query.id, [r], cache_time=60)

@bot.inline_handler(lambda query: not query.query)
def inline_default(query):
    r = InlineQueryResultArticle(
        id="help",
        title="🔍 Fedresurs Inline",
        description="Напиши ИНН (10-12 цифр)",
        input_message_content=InputTextMessageContent("💡 Напиши 10-12 цифр ИНН")
    )
    bot.answer_inline_query(query.id, [r])

if __name__ == '__main__':
    logging.info("🚀 Inline Fedresurs Parser запущен")
    bot.polling(none_stop=True)
