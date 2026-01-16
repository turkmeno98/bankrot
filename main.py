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
FEDRESURS_URL = "https://fedresurs.ru"

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
    'Referer': f'{FEDRESURS_URL}/search/entity',
    'Content-Type': 'application/json'
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
        # ✅ ПРЯМОЙ БЕКЕНД ПОИСК ПО ИНН (как в Habr)
        if len(inn) == 10:
            endpoint = "companies"
        elif len(inn) == 12:
            endpoint = "persons"  
        else:
            return f"❌ ИНН `{inn}`: 10/12 цифр"
        
        # Шаг 1: Поиск по code=ИНН
        search_url = f"{FEDRESURS_URL}/backend/{endpoint}?limit=1&offset=0&code={inn}"
        resp = session.get(search_url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if not data.get('pageData'):
            return f"❌ ИНН `{inn}` не найден в ЕФРСБ"
        
        person = data['pageData'][0]
        guid = person['guid']
        name = person.get('shortName', person.get('fullName', 'N/A'))[:50]
        
        # Шаг 2: Публикации банкрота
        pubs_url = f"{FEDRESURS_URL}/backend/{endpoint}/{guid}/publications"
        params = {
            'limit': 10, 'offset': 0,
            'searchPersonEfrsbMessage': 'true',
            'searchPersonBankruptMessage': 'true',
            'searchAmReport': 'true'
        }
        session.headers['Referer'] = f"{FEDRESURS_URL}/persons/{guid}"
        resp_pubs = session.get(pubs_url, params=params, timeout=15)
        pubs_data = resp_pubs.json()
        
        pubs_count = pubs_data.get('total', 0)
        result = f"✅ *{name}*\n"
        result += f"`ИНН: {inn}` | 👤 {endpoint}\n"
        result += f"📄 Публикаций: *{pubs_count}*\n🔗 [{FEDRESURS_URL}/persons/{guid}]\n\n"
        
        if pubs_data.get('pageData'):
            result += "📋 *Последние публикации:*\n\n"
            for item in pubs_data['pageData'][:6]:
                number = item.get('number', 'N/A')
                type_name = item.get('typeName', item.get('type', 'Неизвестно'))[:30]
                date = item.get('datePublish', 'N/A')[:10]
                result += f"• `{number}` | {type_name}\n  _{date}_\n\n"
        
        return result[:4000]
    except Exception as e:
        logging.error(f"Parse error {inn}: {e}")
        return f"💥 Поиск `{inn}`: временная ошибка сервера"

init_db()

@bot.message_handler(commands=['start', 'help'])
def start(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton('/add_inn'), KeyboardButton('/my_inns'))
    markup.add(KeyboardButton('/clear_inns'))
    bot.send_message(message.chat.id,
        "🔍 *Fedresurs Inline Bot*\n\n"
        "`/add_inn 340735628010` ← Твой ИНН!\n"
        "`@botname 340735628010` ← Inline\n"
        "`/my_inns` ← Кнопки", 
        reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(commands=['add_inn'])
def add_inn_cmd(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❌ `/add_inn 340735628010`", parse_mode='Markdown')
        return
    
    inn = re.sub(r'\D', '', parts[1])[:12]
    if len(inn) not in [10, 12]:
        bot.reply_to(message, "❌ Только 10/12 цифр")
        return
    
    add_inn(message.from_user.id, inn)
    bot.reply_to(message, f"⏳ Сохраняем `{inn}`...")
    
    result = parse_bankrot(inn)
    bot.reply_to(message, result, parse_mode='Markdown')

@bot.message_handler(commands=['my_inns'])
def my_inns(message):
    inns = get_user_inns(message.from_user.id)
    if not inns:
        bot.reply_to(message, "📝 `/add_inn 340735628010`")
        return
    
    markup = InlineKeyboardMarkup(row_width=1)
    for inn in inns:
        markup.add(InlineKeyboardButton(inn, callback_data=f"search:{inn}"))
    
    text = f"📋 *Твои ИНН* ({len(inns)}):\n\n" + "\n".join([f"• `{inn}`" for inn in inns])
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(commands=['clear_inns'])
def clear_inns(message):
    conn = sqlite3.connect('inns.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("DELETE FROM user_inns WHERE user_id = ?", (message.from_user.id,))
    conn.commit()
    conn.close()
    bot.reply_to(message, "🗑 Очищено")

@bot.callback_query_handler(func=lambda call: call.data.startswith('search:'))
def callback_search(call):
    inn = call.data[7:]
    bot.answer_callback_query(call.id, "🔍")
    result = parse_bankrot(inn)
    bot.edit_message_text(result, call.message.chat.id, call.message.message_id, parse_mode='Markdown')

# ✅ Inline обработчики
@bot.inline_handler(lambda query: bool(query.query))
def inline_query(query):
    inn = re.sub(r'\D', '', query.query)[:12]
    if len(inn) not in [10, 12]:
        r = InlineQueryResultArticle(id="err", title="Ошибка", 
            input_message_content=InputTextMessageContent("💡 10-12 цифр"))
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
        id="help", title="Fedresurs", 
        input_message_content=InputTextMessageContent("🔍 Напиши ИНН")
    )
    bot.answer_inline_query(query.id, [r])

if __name__ == '__main__':
    logging.info("🚀 Fedresurs Bot ✅")
    bot.polling(none_stop=True)
