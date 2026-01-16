import os
import sqlite3
import requests
import re
import logging
from telebot import TeleBot
from telebot.types import InlineQueryResultArticle, InputTextMessageContent, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import telebot
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
bot = TeleBot(os.getenv('BOT_TOKEN'))
BANKROT_URL = "https://bankrot.fedresurs.ru"  # Правильный домен!

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Referer': f'{BANKROT_URL}/',
    'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="122", "Google Chrome";v="122"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"'
})

def init_db():
    conn = sqlite3.connect('inns.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_inns 
                 (user_id INTEGER, inn TEXT PRIMARY KEY, created_at TEXT)''')
    conn.commit()
    conn.close()

def add_inn(user_id, inn):
    conn = sqlite3.connect('inns.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_inns (user_id, inn, created_at) VALUES (?, ?, ?)",
              (user_id, inn, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_user_inns(user_id):
    conn = sqlite3.connect('inns.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT inn FROM user_inns WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (user_id,))
    return [row[0] for row in c.fetchall()]

def parse_bankrot(inn):
    try:
        # Поиск на bankrot.fedresurs.ru
        search_url = f"{BANKROT_URL}/search/advanced"
        data = {
            'inn': inn,
            'searchType': 'all'
        }
        resp = session.post(search_url, data=data, timeout=15)
        
        # Ищем GUID в результатах
        guid_match = re.search(r'"guid":"([a-f0-9\-]+)"', resp.text)
        if not guid_match:
            return f"❌ ИНН `{inn}` не найден"
        
        guid = guid_match.group(1)
        
        # Публикации
        pubs_url = f"{BANKROT_URL}/backend/persons/{guid}/publications"  # или companies
        params = {'limit': 10, 'offset': 0}
        resp_pubs = session.get(pubs_url, params=params, timeout=10)
        pubs = resp_pubs.json()
        
        if not pubs.get('pageData'):
            return f"✅ ИНН `{inn}` найден, но публикаций нет"
        
        result = f"✅ *Банкротства ИНН {inn}* ({pubs['total']} шт.)\n\n"
        for item in pubs['pageData'][:8]:
            number = item.get('number', 'N/A')
            type_name = item.get('typeName', item.get('type', 'N/A'))[:25]
            date = item.get('datePublish', 'N/A')[:10]
            result += f"• `{number}` | {type_name} | {date}\n"
        return result
    except Exception as e:
        logging.error(f"Parse error {inn}: {e}")
        return f"💥 Поиск {inn} временно недоступен"

init_db()

@bot.message_handler(commands=['start', 'help'])
def start(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton('/add_inn'), KeyboardButton('/my_inns'))
    markup.add(KeyboardButton('/clear_inns'))
    bot.send_message(message.chat.id,
        "🔍 *Fedresurs Inline Parser*\n\n"
        "`/add_inn 340735628010` — сохранить\n"
        "`@botname 340735628010` — inline\n"
        "`/my_inns` — список с кнопками", 
        reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(commands=['add_inn'])
def add_inn_cmd(message):
    try:
        parts = message.text.split()
        if len(parts) < 2:
            return bot.reply_to(message, "❌ `/add_inn 340735628010`", parse_mode='Markdown')
        
        inn = parts[1][:12].strip()  # Нормализуем
        if not inn.isdigit() or len(inn) not in [10, 12]:
            return bot.reply_to(message, "❌ ИНН: 10 или 12 цифр")
        
        add_inn(message.from_user.id, inn)
        result = parse_bankrot(inn)  # Сразу ищем!
        bot.reply_to(message, f"✅ `{inn}` *сохранен*\n\n{result}", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"💥 Ошибка: {str(e)}")
        logging.error(f"add_inn error: {e}")

@bot.message_handler(commands=['my_inns'])
def my_inns(message):
    inns = get_user_inns(message.from_user.id)
    if not inns:
        return bot.reply_to(message, "📝 Сохрани первый: `/add_inn 340735628010`", parse_mode='Markdown')
    
    markup = InlineKeyboardMarkup(row_width=1)
    for inn in inns:
        markup.add(InlineKeyboardButton(f"🔍 {inn}", callback_data=f"search:{inn}"))
    
    text = f"📋 *Твои ИНН* ({len(inns)}):\n\n" + "\n".join([f"• `{inn}`" for inn in inns])
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(commands=['clear_inns'])
def clear_inns(message):
    conn = sqlite3.connect('inns.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("DELETE FROM user_inns WHERE user_id = ?", (message.from_user.id,))
    conn.commit()
    conn.close()
    bot.reply_to(message, "🗑 Все ИНН удалены")

@bot.callback_query_handler(func=lambda call: call.data.startswith('search:'))
def callback_search(call):
    inn = call.data[7:]
    bot.answer_callback_query(call.id, "🔍 Ищем...")
    result = parse_bankrot(inn)
    bot.edit_message_text(result, call.message.chat.id, call.message.message_id, parse_mode='Markdown')

@bot.inline_handler(lambda query: query.query)
def inline_query(query):
    inn = re.sub(r'\D', '', query.query)[:12]  # Только цифры
    if len(inn) not in [10, 12]:
        r = InlineQueryResultArticle(
            id="error",
            title="Ошибка",
            description="Нужен ИНН (10/12 цифр)",
            input_message_content=InputTextMessageContent("💡 Напиши 10-12 цифр")
        )
        return bot.answer_inline_query(query.id, [r])
    
    result = parse_bankrot(inn)
    r = InlineQueryResultArticle(
        id=inn,
        title=f"Банкротства {inn}",
        description=result[:100],
        input_message_content=InputTextMessageContent(result, parse_mode='Markdown')
    )
    bot.answer_inline_query(query.id, [r], cache_time=300)

@bot.inline_handler()
def inline_empty(query):
    r = InlineQueryResultArticle(
        id="help",
        title="Fedresurs Inline",
        description="Напиши ИНН 340735628010",
        input_message_content=InputTextMessageContent("🔍 Напиши 10-12 цифр")
    )
    bot.answer_inline_query(query.id, [r])

if __name__ == '__main__':
    logging.info("🚀 Inline Fedresurs Bot started")
    bot.polling(none_stop=True, interval=1)
