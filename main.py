import os
import sqlite3
import requests
import re
import logging
from telebot import TeleBot
from telebot.types import InlineQueryResultArticle, InputTextMessageContent, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
bot = TeleBot(os.getenv('BOT_TOKEN'))
BANKROT_URL = "https://bankrot.fedresurs.ru"

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
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
        # Поиск через GET (упрощено)
        params = {'inn': inn}
        resp = session.get(f"{BANKROT_URL}/search-results", params=params, timeout=15)
        
        # Ищем GUID
        guid_match = re.search(r'"guid":"([a-f0-9\-]+)', resp.text)
        if not guid_match:
            return f"❌ ИНН `{inn}` не найден в ЕФРСБ"
        
        guid = guid_match.group(1)
        
        # Публикации (попытка persons/companies)
        for entity in ['persons', 'companies']:
            try:
                pubs_url = f"{BANKROT_URL}/backend/{entity}/{guid}/publications"
                resp_pubs = session.get(pubs_url, params={'limit': 10}, timeout=10)
                if resp_pubs.status_code == 200:
                    pubs = resp_pubs.json()
                    if pubs.get('pageData'):
                        result = f"✅ *Банкротства ИНН {inn}*\n({pubs['total']} публикаций)\n\n"
                        for item in pubs['pageData'][:8]:
                            number = item.get('number', 'N/A')
                            type_name = item.get('typeName', 'Неизвестно')
                            date = item.get('datePublish', '')[:10]
                            result += f"• `{number}`\n  {type_name} | {date}\n\n"
                        return result
            except:
                continue
        
        return f"✅ ИНН `{inn}` найден, публикаций нет"
    except Exception as e:
        logging.error(f"Parse {inn}: {e}")
        return f"💥 Сервис временно недоступен"

init_db()

@bot.message_handler(commands=['start', 'help'])
def start(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton('/add_inn'), KeyboardButton('/my_inns'))
    markup.add(KeyboardButton('/clear_inns'))
    bot.send_message(message.chat.id,
        "🔍 *Fedresurs Inline Bot*\n\n"
        "`/add_inn 340735628010` — сохранить + поиск\n"
        "`@yourbot 340735628010` — inline\n"
        "`/my_inns` — кнопки поиска", 
        reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(commands=['add_inn'])
def add_inn_cmd(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❌ `/add_inn 340735628010`", parse_mode='Markdown')
        return
    
    inn = re.sub(r'\D', '', parts[1])[:12]
    if len(inn) not in [10, 12] or not inn.isdigit():
        bot.reply_to(message, "❌ ИНН: только 10/12 цифр")
        return
    
    add_inn(message.from_user.id, inn)
    bot.reply_to(message, f"⏳ Сохраняем `{inn}` и ищем...")
    
    result = parse_bankrot(inn)
    bot.reply_to(message, result, parse_mode='Markdown')

@bot.message_handler(commands=['my_inns'])
def my_inns(message):
    inns = get_user_inns(message.from_user.id)
    if not inns:
        bot.reply_to(message, "📝 `/add_inn 340735628010` — сохрани первый")
        return
    
    markup = InlineKeyboardMarkup(row_width=1)
    for inn in inns:
        markup.add(InlineKeyboardButton(inn, callback_data=f"search:{inn}"))
    
    text = f"📋 `{message.from_user.first_name}`, твои ИНН ({len(inns)}):\n\n" + "\n".join([f"• `{inn}`" for inn in inns])
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(commands=['clear_inns'])
def clear_inns(message):
    conn = sqlite3.connect('inns.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("DELETE FROM user_inns WHERE user_id = ?", (message.from_user.id,))
    conn.commit()
    conn.close()
    bot.reply_to(message, "🗑 ИНН удалены")

@bot.callback_query_handler(func=lambda call: call.data.startswith('search:'))
def callback_search(call):
    inn = call.data.split(':', 1)[1]
    bot.answer_callback_query(call.id, "🔍 Ищем...")
    result = parse_bankrot(inn)
    bot.edit_message_text(result, call.message.chat.id, call.message.message_id, parse_mode='Markdown')

# ✅ ИСПРАВЛЕННЫЕ INLINE ОБРАБОТЧИКИ
@bot.inline_handler(lambda query: bool(query.query))
def inline_query(query):
    inn = re.sub(r'\D', '', query.query)[:12]
    if len(inn) not in [10, 12]:
        r = InlineQueryResultArticle(
            id="error", title="Ошибка ИНН", 
            input_message_content=InputTextMessageContent("💡 10-12 цифр")
        )
        bot.answer_inline_query(query.id, [r])
        return
    
    result = parse_bankrot(inn)
    r = InlineQueryResultArticle(
        id=inn, title=f"ИНН {inn}",
        description=result[:100],
        input_message_content=InputTextMessageContent(result, parse_mode='Markdown')
    )
    bot.answer_inline_query(query.id, [r], cache_time=300)

@bot.inline_handler(func=lambda query: not query.query)
def inline_empty(query):
    r = InlineQueryResultArticle(
        id="help", title="🔍 Fedresurs",
        description="340735628010",
        input_message_content=InputTextMessageContent("Напиши ИНН (10-12 цифр)")
    )
    bot.answer_inline_query(query.id, [r])

if __name__ == '__main__':
    logging.info("🚀 Fedresurs Inline Bot ✅")
    bot.polling(none_stop=True, interval=1, timeout=30)
