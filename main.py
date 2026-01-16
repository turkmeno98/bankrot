import os
import sqlite3
import requests
import re
import logging
import json
from telebot import TeleBot
from telebot.types import (InlineKeyboardMarkup, InlineKeyboardButton, 
                          InlineQueryResultArticle, InputTextMessageContent)
from datetime import datetime, timedelta
from contextlib import contextmanager
from bs4 import BeautifulSoup
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Настройки
bot = TeleBot(os.getenv('BOT_TOKEN'))
FEDRESURS_URL = "https://fedresurs.ru"
FEDRESURS_LOGIN = os.getenv('FEDRESURS_LOGIN', '')
FEDRESURS_PASSWORD = os.getenv('FEDRESURS_PASSWORD', '')
DB_PATH = 'inns.db'

# Сессия
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
})

is_logged_in = False

# ============== AUTH ==============
def login():
    """Авторизация на Fedresurs"""
    global is_logged_in
    
    if not FEDRESURS_LOGIN or not FEDRESURS_PASSWORD:
        logger.warning("⚠️ Логин/пароль не указаны")
        return False
    
    try:
        logger.info("🔐 Попытка авторизации...")
        
        # Шаг 1: Получаем страницу входа
        login_url = f"{FEDRESURS_URL}/common/login?tab=monitoring"
        resp = session.get(login_url, timeout=15)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Ищем CSRF токен
        csrf_input = soup.find('input', {'name': re.compile(r'csrf|_csrf|authenticity_token', re.I)})
        csrf_token = csrf_input.get('value') if csrf_input else None
        
        # Ищем форму
        form = soup.find('form')
        action = form.get('action', '/common/login') if form else '/common/login'
        if not action.startswith('http'):
            action = FEDRESURS_URL + action
        
        logger.info(f"📝 Форма: {action}")
        if csrf_token:
            logger.info(f"🔑 CSRF токен найден")
        
        # Шаг 2: Отправляем данные авторизации
        login_data = {
            'username': FEDRESURS_LOGIN,
            'password': FEDRESURS_PASSWORD,
            'tab': 'monitoring'
        }
        
        if csrf_token:
            login_data['_csrf'] = csrf_token
            login_data['csrf_token'] = csrf_token
        
        # Обновляем заголовки
        session.headers.update({
            'Content-Type': 'application/x-www-form-urlencoded',
            'Referer': login_url
        })
        
        resp = session.post(action, data=login_data, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        
        logger.info(f"📡 Ответ: {resp.status_code}, URL: {resp.url}")
        
        # Проверяем успешность
        if 'logout' in resp.text.lower() or 'выход' in resp.text.lower() or 'monitoring' in resp.url:
            logger.info("✅ Авторизация успешна!")
            is_logged_in = True
            
            # Сохраняем cookies
            with open('cookies.txt', 'w') as f:
                for cookie in session.cookies:
                    f.write(f"{cookie.name}={cookie.value}\n")
            
            return True
        else:
            logger.warning("⚠️ Авторизация не удалась")
            # Сохраняем ответ для отладки
            with open('login_response.html', 'w', encoding='utf-8') as f:
                f.write(resp.text)
            return False
            
    except Exception as e:
        logger.error(f"❌ Ошибка авторизации: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def ensure_logged_in():
    """Проверка и переавторизация при необходимости"""
    global is_logged_in
    if not is_logged_in:
        return login()
    return True

# ============== DATABASE ==============
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        
        # Таблица отслеживаемых ИНН
        c.execute('''CREATE TABLE IF NOT EXISTS monitored_inns 
                     (user_id INTEGER, 
                      inn TEXT, 
                      name TEXT,
                      notify INTEGER DEFAULT 1,
                      created_at TEXT,
                      PRIMARY KEY (user_id, inn))''')
        
        # Таблица публикаций (для отслеживания новых)
        c.execute('''CREATE TABLE IF NOT EXISTS publications 
                     (pub_id TEXT PRIMARY KEY,
                      inn TEXT,
                      number TEXT,
                      date TEXT,
                      type TEXT,
                      seen_at TEXT)''')
        
        # Настройки
        c.execute('''CREATE TABLE IF NOT EXISTS user_settings 
                     (user_id INTEGER PRIMARY KEY, 
                      notify_enabled INTEGER DEFAULT 1,
                      show_details INTEGER DEFAULT 1,
                      max_pubs INTEGER DEFAULT 5)''')
        
        conn.commit()
        logger.info("✅ БД инициализирована")

def add_monitored_inn(user_id, inn, name=""):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""INSERT OR REPLACE INTO monitored_inns 
                        (user_id, inn, name, notify, created_at) 
                        VALUES (?, ?, ?, 1, ?)""",
                     (user_id, inn, name, datetime.now().isoformat()))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка добавления ИНН: {e}")
        return False

def get_monitored_inns(user_id):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""SELECT inn, name, notify FROM monitored_inns 
                        WHERE user_id = ? 
                        ORDER BY created_at DESC""", (user_id,))
            return c.fetchall()
    except:
        return []

def delete_monitored_inn(user_id, inn):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM monitored_inns WHERE user_id = ? AND inn = ?", (user_id, inn))
            conn.commit()
            return True
    except:
        return False

def toggle_notify(user_id, inn):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("UPDATE monitored_inns SET notify = 1 - notify WHERE user_id = ? AND inn = ?", 
                     (user_id, inn))
            conn.commit()
            c.execute("SELECT notify FROM monitored_inns WHERE user_id = ? AND inn = ?", (user_id, inn))
            row = c.fetchone()
            return bool(row[0]) if row else False
    except:
        return False

def save_publication(inn, pub):
    """Сохраняет публикацию в БД"""
    try:
        pub_id = f"{inn}_{pub['number']}"
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""INSERT OR IGNORE INTO publications 
                        (pub_id, inn, number, date, type, seen_at) 
                        VALUES (?, ?, ?, ?, ?, ?)""",
                     (pub_id, inn, pub['number'], pub['date'], pub['type'], 
                      datetime.now().isoformat()))
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка сохранения публикации: {e}")

def is_new_publication(inn, pub_number):
    """Проверяет, новая ли публикация"""
    try:
        pub_id = f"{inn}_{pub_number}"
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT pub_id FROM publications WHERE pub_id = ?", (pub_id,))
            return c.fetchone() is None
    except:
        return True

def get_user_settings(user_id):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT notify_enabled, show_details, max_pubs FROM user_settings WHERE user_id = ?", 
                     (user_id,))
            row = c.fetchone()
            if row:
                return {'notify_enabled': bool(row[0]), 'show_details': bool(row[1]), 'max_pubs': row[2]}
            return {'notify_enabled': True, 'show_details': True, 'max_pubs': 5}
    except:
        return {'notify_enabled': True, 'show_details': True, 'max_pubs': 5}

def update_user_settings(user_id, **kwargs):
    try:
        current = get_user_settings(user_id)
        current.update(kwargs)
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""INSERT OR REPLACE INTO user_settings 
                        (user_id, notify_enabled, show_details, max_pubs) 
                        VALUES (?, ?, ?, ?)""",
                     (user_id, int(current['notify_enabled']), 
                      int(current['show_details']), current['max_pubs']))
            conn.commit()
            return True
    except:
        return False

# ============== FEDRESURS PARSER ==============
def search_inn(inn):
    """Поиск по ИНН через мониторинг"""
    try:
        if not ensure_logged_in():
            return None
        
        inn = re.sub(r'\D', '', inn)[:12]
        logger.info(f"🔍 Поиск ИНН {inn}...")
        
        # Пробуем API мониторинга
        search_url = f"{FEDRESURS_URL}/monitoring/api/search"
        
        params = {
            'inn': inn,
            'limit': 15
        }
        
        time.sleep(0.5)
        resp = session.get(search_url, params=params, timeout=20)
        
        if resp.status_code == 200:
            try:
                data = resp.json()
                logger.info(f"✅ API ответ: {json.dumps(data, ensure_ascii=False)[:200]}")
                return data
            except:
                pass
        
        # Если API не работает, пробуем обычный поиск
        search_url = f"{FEDRESURS_URL}/search"
        params = {
            'query': inn,
            'type': 'entity'
        }
        
        resp = session.get(search_url, params=params, timeout=20)
        resp.raise_for_status()
        
        # Сохраняем для отладки
        with open(f'search_result_{inn}.html', 'w', encoding='utf-8') as f:
            f.write(resp.text)
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Ищем данные в JSON внутри страницы
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and inn in script.string:
                # Пробуем извлечь JSON
                json_match = re.search(r'\{[^{}]*"inn"\s*:\s*"' + inn + r'"[^{}]*\}', script.string)
                if json_match:
                    try:
                        entity_data = json.loads(json_match.group())
                        return entity_data
                    except:
                        pass
        
        # Ищем ID сущности в ссылках
        entity_link = soup.find('a', href=re.compile(r'/entity/'))
        if entity_link:
            entity_id = entity_link['href'].split('/')[-1]
            return {'id': entity_id, 'inn': inn}
        
        return None
        
    except Exception as e:
        logger.error(f"Ошибка поиска ИНН {inn}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

def get_publications(inn, max_pubs=10):
    """Получает публикации по ИНН"""
    try:
        if not ensure_logged_in():
            return {'success': False, 'message': 'Ошибка авторизации'}
        
        inn = re.sub(r'\D', '', inn)[:12]
        logger.info(f"📄 Получение публикаций для ИНН {inn}...")
        
        # Пробуем разные endpoints
        endpoints = [
            f"{FEDRESURS_URL}/monitoring/api/publications?inn={inn}&limit={max_pubs}",
            f"{FEDRESURS_URL}/api/search/publications?inn={inn}&limit={max_pubs}",
            f"{FEDRESURS_URL}/backend/persons/search?code={inn}" if len(inn) == 12 else f"{FEDRESURS_URL}/backend/companies/search?code={inn}",
        ]
        
        for endpoint in endpoints:
            try:
                logger.info(f"Попытка: {endpoint}")
                time.sleep(0.5)
                resp = session.get(endpoint, timeout=15)
                
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(f"Ответ: {json.dumps(data, ensure_ascii=False)[:300]}")
                    
                    # Парсим разные форматы ответа
                    publications = []
                    name = None
                    total = 0
                    
                    # Формат 1: прямой список публикаций
                    if isinstance(data, dict):
                        if 'publications' in data:
                            publications = data['publications']
                            total = data.get('total', len(publications))
                        elif 'items' in data:
                            publications = data['items']
                            total = data.get('total', len(publications))
                        elif 'pageData' in data:
                            publications = data['pageData']
                            total = data.get('total', len(publications))
                        
                        # Извлекаем имя
                        if 'entity' in data:
                            name = data['entity'].get('name') or data['entity'].get('fullName')
                        elif data.get('name'):
                            name = data['name']
                    
                    elif isinstance(data, list):
                        publications = data
                        total = len(data)
                    
                    if publications or name:
                        # Форматируем публикации
                        formatted_pubs = []
                        for pub in publications[:max_pubs]:
                            formatted_pubs.append({
                                'number': pub.get('number', 'Б/Н'),
                                'type': pub.get('typeName') or pub.get('messageType') or pub.get('type', 'Не указан'),
                                'date': (pub.get('publishDate') or pub.get('datePublish') or pub.get('date', ''))[:10] or 'Нет даты'
                            })
                            
                            # Сохраняем в БД
                            save_publication(inn, formatted_pubs[-1])
                        
                        return {
                            'success': True,
                            'inn': inn,
                            'name': name or 'Не указано',
                            'total_pubs': total,
                            'publications': formatted_pubs
                        }
            
            except Exception as e:
                logger.warning(f"Endpoint не сработал: {e}")
                continue
        
        # Если ничего не нашли
        return {
            'success': False,
            'message': f"❌ Данные по ИНН `{inn}` не найдены\n\n💡 Проверьте:\n• Правильность ИНН\n• Наличие авторизации\n• Доступ к разделу мониторинга"
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения публикаций: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'success': False,
            'message': f"💥 Ошибка обработки\n\nСмотрите bot.log"
        }

def format_result(data, show_details=True):
    """Форматирование результата"""
    if not data.get('success'):
        return data.get('message', '❌ Ошибка')
    
    entity_type = "🏢 Юрлицо" if len(data['inn']) == 10 else "👤 Физлицо"
    
    result = f"*{data['name']}*\n{entity_type} | ИНН: `{data['inn']}`\n"
    result += f"📊 Публикаций в мониторинге: *{data['total_pubs']}*\n"
    
    if data['total_pubs'] == 0:
        result += "\n✅ *Публикаций нет*"
        return result
    
    if show_details and data.get('publications'):
        result += "\n━━━━━━━━━━━━━━━━━━\n📄 *Публикации:*\n\n"
        for i, pub in enumerate(data['publications'], 1):
            is_new = is_new_publication(data['inn'], pub['number'])
            new_badge = "🆕 " if is_new else ""
            
            result += f"{new_badge}*{i}.* №`{pub['number']}`\n"
            result += f"   📌 {pub['type'][:50]}\n"
            result += f"   📅 {pub['date']}\n\n"
        
        if data['total_pubs'] > len(data['publications']):
            result += f"_... ещё {data['total_pubs'] - len(data['publications'])}_\n"
    
    return result[:4096]

# ============== KEYBOARDS ==============
def get_main_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔍 Поиск по ИНН", callback_data="search"),
        InlineKeyboardButton("⭐️ Мониторинг", callback_data="monitoring")
    )
    markup.add(
        InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
        InlineKeyboardButton("ℹ️ Справка", callback_data="help")
    )
    return markup

def get_back_button():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("◀️ Назад", callback_data="menu"))
    return markup

def get_monitoring_menu(inns):
    markup = InlineKeyboardMarkup(row_width=1)
    
    for inn, name, notify in inns:
        notify_icon = "🔔" if notify else "🔕"
        text = f"{notify_icon} {name[:18] if name else inn}"
        markup.add(InlineKeyboardButton(text, callback_data=f"mon:{inn}"))
    
    markup.add(
        InlineKeyboardButton("➕ Добавить ИНН", callback_data="add_monitoring"),
        InlineKeyboardButton("◀️ Главное меню", callback_data="menu")
    )
    return markup

def get_monitoring_item_menu(inn, notify):
    markup = InlineKeyboardMarkup(row_width=2)
    notify_text = "🔕 Выключить" if notify else "🔔 Включить"
    markup.add(
        InlineKeyboardButton(notify_text, callback_data=f"toggle:{inn}"),
        InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh:{inn}")
    )
    markup.add(
        InlineKeyboardButton("❌ Удалить", callback_data=f"del_mon:{inn}"),
        InlineKeyboardButton("◀️ Мониторинг", callback_data="monitoring")
    )
    return markup

def get_settings_menu(user_id):
    settings = get_user_settings(user_id)
    markup = InlineKeyboardMarkup(row_width=1)
    
    notify_status = "✅" if settings['notify_enabled'] else "❌"
    detail_status = "✅" if settings['show_details'] else "❌"
    
    markup.add(
        InlineKeyboardButton(f"{notify_status} Уведомления", callback_data="toggle_notify"),
        InlineKeyboardButton(f"{detail_status} Детали", callback_data="toggle_details"),
        InlineKeyboardButton(f"📊 Публикаций: {settings['max_pubs']}", callback_data="change_pubs")
    )
    markup.add(InlineKeyboardButton("◀️ Меню", callback_data="menu"))
    return markup

def get_max_pubs_menu():
    markup = InlineKeyboardMarkup(row_width=3)
    for num in [3, 5, 10]:
        markup.add(InlineKeyboardButton(str(num), callback_data=f"pubs:{num}"))
    markup.add(InlineKeyboardButton("◀️ Назад", callback_data="settings"))
    return markup

# ============== HANDLERS ==============
@bot.message_handler(commands=['start'])
def start(msg):
    # Пробуем авторизоваться при старте
    if FEDRESURS_LOGIN and FEDRESURS_PASSWORD and not is_logged_in:
        login()
    
    auth_status = "🔐 Авторизован" if is_logged_in else "⚠️ Без авторизации"
    
    text = (
        "🔍 *Fedresurs Monitoring Bot*\n\n"
        f"{auth_status}\n\n"
        "💡 *Возможности:*\n"
        "• Поиск банкротств по ИНН\n"
        "• Мониторинг новых публикаций\n"
        "• Уведомления о новых записях\n\n"
        "📝 Отправьте ИНН (10 или 12 цифр)"
    )
    bot.send_message(msg.chat.id, text, parse_mode='Markdown', reply_markup=get_main_menu())

@bot.message_handler(func=lambda m: True)
def handle_msg(msg):
    inn = re.sub(r'\D', '', msg.text)[:12]
    if len(inn) in [10, 12]:
        wait_msg = bot.send_message(msg.chat.id, "🔍 Поиск в мониторинге...")
        
        settings = get_user_settings(msg.from_user.id)
        data = get_publications(inn, settings['max_pubs'])
        result = format_result(data, settings['show_details'])
        
        # Проверяем, отслеживается ли
        monitored = get_monitored_inns(msg.from_user.id)
        is_monitored = any(i == inn for i, _, _ in monitored)
        
        markup = InlineKeyboardMarkup(row_width=2)
        if not is_monitored and data.get('success'):
            markup.add(InlineKeyboardButton("⭐️ Добавить в мониторинг", callback_data=f"add_mon:{inn}"))
        markup.add(
            InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh:{inn}"),
            InlineKeyboardButton("◀️ Меню", callback_data="menu")
        )
        
        bot.edit_message_text(result, msg.chat.id, wait_msg.message_id, 
                            parse_mode='Markdown', reply_markup=markup,
                            disable_web_page_preview=True)
    else:
        bot.send_message(msg.chat.id, "❌ Неверный формат\nОтправьте ИНН (10 или 12 цифр)",
                        reply_markup=get_main_menu())

@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    uid = call.from_user.id
    d = call.data
    
    try:
        if d == "menu":
            auth_status = "🔐 Авторизован" if is_logged_in else "⚠️ Без авторизации"
            bot.edit_message_text(f"🏠 Главное меню\n\n{auth_status}", 
                                call.message.chat.id, call.message.message_id,
                                reply_markup=get_main_menu())
        
        elif d == "search":
            bot.edit_message_text("🔍 *Поиск по ИНН*\n\nОтправьте ИНН (10 или 12 цифр)\n\nПример: `340735628010`", 
                                call.message.chat.id, call.message.message_id,
                                parse_mode='Markdown', reply_markup=get_back_button())
        
        elif d == "monitoring":
            inns = get_monitored_inns(uid)
            if not inns:
                bot.edit_message_text("⭐️ *Мониторинг*\n\nУ вас нет отслеживаемых ИНН\n\n💡 Найдите ИНН и добавьте в мониторинг", 
                                    call.message.chat.id, call.message.message_id,
                                    parse_mode='Markdown', reply_markup=get_back_button())
            else:
                bot.edit_message_text(f"⭐️ *Мониторинг* ({len(inns)} ИНН)\n\n🔔 — уведомления включены\n🔕 — выключены", 
                                    call.message.chat.id, call.message.message_id,
                                    parse_mode='Markdown', reply_markup=get_monitoring_menu(inns))
        
        elif d.startswith("mon:"):
            inn = d.split(":")[1]
            bot.edit_message_text("🔍 Загрузка...", call.message.chat.id, call.message.message_id)
            
            settings = get_user_settings(uid)
            data = get_publications(inn, settings['max_pubs'])
            result = format_result(data, settings['show_details'])
            
            monitored = get_monitored_inns(uid)
            notify = next((n for i, _, n in monitored if i == inn), False)
            
            bot.edit_message_text(result, call.message.chat.id, call.message.message_id,
                                parse_mode='Markdown', reply_markup=get_monitoring_item_menu(inn, notify),
                                disable_web_page_preview=True)
        
        elif d.startswith("add_mon:"):
            inn = d.split(":")[1]
            data = get_publications(inn, 1)
            name = data.get('name', '') if data.get('success') else ''
            
            if add_monitored_inn(uid, inn, name):
                bot.answer_callback_query(call.id, "⭐️ Добавлено в мониторинг")
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                            reply_markup=InlineKeyboardMarkup().add(
                                                InlineKeyboardButton("◀️ Меню", callback_data="menu")
                                            ))
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка добавления")
        
        elif d.startswith("del_mon:"):
            inn = d.split(":")[1]
            if delete_monitored_inn(uid, inn):
                bot.answer_callback_query(call.id, "❌ Удалено из мониторинга")
                inns = get_monitored_inns(uid)
                if inns:
                    bot.edit_message_text(f"⭐️ *Мониторинг* ({len(inns)} ИНН)", 
                                        call.message.chat.id, call.message.message_id,
                                        parse_mode='Markdown', reply_markup=get_monitoring_menu(inns))
                else:
                    bot.edit_message_text("⭐️ Мониторинг очищен", 
                                        call.message.chat.id, call.message.message_id,
                                        reply_markup=get_back_button())
        
        elif d.startswith("toggle:"):
            inn = d.split(":")[1]
            new_state = toggle_notify(uid, inn)
            status = "включены" if new_state else "выключены"
            bot.answer_callback_query(call.id, f"🔔 Уведомления {status}")
            
            monitored = get_monitored_inns(uid)
            notify = next((n for i, _, n in monitored if i == inn), False)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                        reply_markup=get_monitoring_item_menu(inn, notify))
        
        elif d.startswith("refresh:"):
            inn = d.split(":")[1]
            bot.answer_callback_query(call.id, "🔄 Обновление...")
            
            settings = get_user_settings(uid)
            data = get_publications(inn, settings['max_pubs'])
            result = format_result(data, settings['show_details'])
            
            monitored = get_monitored_inns(uid)
            is_monitored = any(i == inn for i, _, _ in monitored)
            
            if is_monitored:
                notify = next((n for i, _, n in monitored if i == inn), False)
                markup = get_monitoring_item_menu(inn, notify)
            else:
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("⭐️ В мониторинг", callback_data=f"add_mon:{inn}"),
                    InlineKeyboardButton("◀️ Меню", callback_data="menu")
                )
            
            bot.edit_message_text(result, call.message.chat.id, call.message.message_id,
                                parse_mode='Markdown', reply_markup=markup,
                                disable_web_page_preview=True)
        
        elif d == "settings":
            bot.edit_message_text("⚙️ *Настройки*", call.message.chat.id, call.message.message_id,
                                parse_mode='Markdown', reply_markup=get_settings_menu(uid))
        
        elif d == "toggle_notify":
            settings = get_user_settings(uid)
            new_val = not settings['notify_enabled']
            update_user_settings(uid, notify_enabled=new_val)
            bot.answer_callback_query(call.id, f"🔔 Уведомления {'ВКЛ' if new_val else 'ВЫКЛ'}")
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                        reply_markup=get_settings_menu(uid))
        
        elif d == "toggle_details":
            settings = get_user_settings(uid)
            new_val = not settings['show_details']
            update_user_settings(uid, show_details=new_val)
            bot.answer_callback_query(call.id, f"✅ Детали {'ВКЛ' if new_val else 'ВЫКЛ'}")
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                        reply_markup=get_settings_menu(uid))
        
        elif d == "change_pubs":
            bot.edit_message_text("📊 Количество публикаций", call.message.chat.id, call.message.message_id,
                                reply_markup=get_max_pubs_menu())
        
        elif d.startswith("pubs:"):
            num = int(d.split(":")[1])
            update_user_settings(uid, max_pubs=num)
            bot.answer_callback_query(call.id, f"✅ Установлено: {num}")
            bot.edit_message_text("⚙️ *Настройки*", call.message.chat.id, call.message.message_id,
                                parse_mode='Markdown', reply_markup=get_settings_menu(uid))
        
        elif d == "help":
            text = (
                "ℹ️ *Справка*\n\n"
                "*ЕФРСБ Мониторинг*\n"
                "Отслеживание публикаций о банкротстве\n\n"
                "🔍 *Функции:*\n"
                "• Поиск по ИНН\n"
                "• Мониторинг публикаций\n"
                "• Уведомления о новых записях\n\n"
                "📝 *Формат ИНН:*\n"
                "• 10 цифр — юрлицо\n"
                "• 12 цифр — физлицо\n\n"
                f"🔐 *Авторизация:*\n"
                f"{'✅ Активна' if is_logged_in else '❌ Требуется настройка'}\n\n"
                "[fedresurs.ru/common/login](https://fedresurs.ru/common/login?tab=monitoring)"
            )
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                                parse_mode='Markdown', reply_markup=get_back_button(),
                                disable_web_page_preview=True)
        
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Callback error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        bot.answer_callback_query(call.id, "❌ Ошибка")

# ============== INLINE ==============
@bot.inline_handler(lambda q: bool(q.query))
def inline_query(q):
    inn = re.sub(r'\D', '', q.query)[:12]
    if len(inn) not in [10, 12]:
        r = InlineQueryResultArticle(id="err", title="❌ Неверный ИНН", description="10 или 12 цифр",
            input_message_content=InputTextMessageContent("❌ Неверный формат ИНН"))
        bot.answer_inline_query(q.id, [r], cache_time=1)
        return
    
    data = get_publications(inn, 5)
    result_text = format_result(data, True)
    title = f"✅ {data.get('name', '')[:30]}" if data.get('success') else f"❌ {inn}"
    desc = f"Публикаций: {data.get('total_pubs', 0)}" if data.get('success') else "Не найден"
    
    r = InlineQueryResultArticle(id=inn, title=title, description=desc,
        input_message_content=InputTextMessageContent(result_text, parse_mode='Markdown', disable_web_page_preview=True))
    bot.answer_inline_query(q.id, [r], cache_time=300)

@bot.inline_handler(func=lambda q: not q.query)
def inline_empty(q):
    r = InlineQueryResultArticle(id="help", title="🔍 Fedresurs Monitoring", description="Введите ИНН",
        input_message_content=InputTextMessageContent("💡 `@botname ИНН`", parse_mode='Markdown'))
    bot.answer_inline_query(q.id, [r], cache_time=300)

# ============== MAIN ==============
if __name__ == '__main__':
    try:
        init_db()
        logger.info("🚀 Fedresurs Monitoring Bot запущен")
        
        # Авторизация при старте
        if FEDRESURS_LOGIN and FEDRESURS_PASSWORD:
            login()
        else:
            logger.warning("⚠️ Логин/пароль не указаны в переменных окружения")
            logger.info("Установите: FEDRESURS_LOGIN и FEDRESURS_PASSWORD")
        
        bot.infinity_polling(timeout=30, long_polling_timeout=30)
    except KeyboardInterrupt:
        logger.info("⛔️ Остановлен")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
        import traceback
        logger.error(traceback.format_exc())
