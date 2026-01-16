import os
import sqlite3
import requests
import re
import logging
from telebot import TeleBot
from telebot.types import (InlineKeyboardMarkup, InlineKeyboardButton, 
                          InlineQueryResultArticle, InputTextMessageContent)
from datetime import datetime
from contextlib import contextmanager

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Инициализация
bot = TeleBot(os.getenv('BOT_TOKEN'))
FEDRESURS_URL = "https://fedresurs.ru"
DB_PATH = 'inns.db'

# Настройки сессии
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ru-RU,ru;q=0.9',
    'Referer': f'{FEDRESURS_URL}/search/entity',
    'Content-Type': 'application/json'
})

# ============== DATABASE ==============
@contextmanager
def get_db():
    """Context manager для безопасной работы с БД"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """Инициализация базы данных"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS user_inns 
                     (user_id INTEGER, 
                      inn TEXT, 
                      name TEXT,
                      created_at TEXT,
                      PRIMARY KEY (user_id, inn))''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_settings 
                     (user_id INTEGER PRIMARY KEY, 
                      show_details INTEGER DEFAULT 1,
                      max_pubs INTEGER DEFAULT 5)''')
        conn.commit()
        logger.info("✅ База данных инициализирована")

def add_inn(user_id, inn, name=""):
    """Добавление ИНН в избранное"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""INSERT OR REPLACE INTO user_inns 
                        (user_id, inn, name, created_at) 
                        VALUES (?, ?, ?, ?)""",
                     (user_id, inn, name, datetime.now().isoformat()))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка добавления ИНН {inn}: {e}")
        return False

def get_user_inns(user_id):
    """Получение списка ИНН пользователя"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""SELECT inn, name FROM user_inns 
                        WHERE user_id = ? 
                        ORDER BY created_at DESC 
                        LIMIT 20""", (user_id,))
            return c.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения ИНН для {user_id}: {e}")
        return []

def delete_inn(user_id, inn):
    """Удаление ИНН из избранного"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM user_inns WHERE user_id = ? AND inn = ?", 
                     (user_id, inn))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка удаления ИНН {inn}: {e}")
        return False

def get_user_settings(user_id):
    """Получение настроек пользователя"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT show_details, max_pubs FROM user_settings WHERE user_id = ?", 
                     (user_id,))
            row = c.fetchone()
            if row:
                return {'show_details': bool(row[0]), 'max_pubs': row[1]}
            return {'show_details': True, 'max_pubs': 5}
    except Exception as e:
        logger.error(f"Ошибка получения настроек для {user_id}: {e}")
        return {'show_details': True, 'max_pubs': 5}

def update_user_settings(user_id, **kwargs):
    """Обновление настроек пользователя"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""INSERT OR REPLACE INTO user_settings 
                        (user_id, show_details, max_pubs) 
                        VALUES (?, ?, ?)""",
                     (user_id, 
                      kwargs.get('show_details', 1),
                      kwargs.get('max_pubs', 5)))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка обновления настроек для {user_id}: {e}")
        return False

# ============== FEDRESURS API ==============
def parse_bankrot(inn, max_pubs=5):
    """Парсинг данных о банкротстве"""
    try:
        inn = re.sub(r'\D', '', inn)[:12]
        
        if len(inn) == 10:
            endpoint = "companies"
        elif len(inn) == 12:
            endpoint = "persons"
        else:
            return {
                'success': False,
                'message': f"❌ Неверный формат ИНН\n\nДолжен быть 10 цифр (юрлицо) или 12 (физлицо)\nВы ввели: `{inn}` ({len(inn)} цифр)"
            }
        
        # Поиск по ИНН
        search_url = f"{FEDRESURS_URL}/backend/{endpoint}"
        params = {'limit': 1, 'offset': 0, 'code': inn}
        
        resp = session.get(search_url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if not data.get('pageData'):
            return {
                'success': False,
                'message': f"❌ ИНН `{inn}` не найден в ЕФРСБ\n\n💡 Проверьте правильность ввода"
            }
        
        person = data['pageData'][0]
        guid = person['guid']
        name = person.get('shortName') or person.get('fullName', 'Не указано')
        full_name = person.get('fullName', name)
        
        # Получение публикаций
        pubs_url = f"{FEDRESURS_URL}/backend/{endpoint}/{guid}/publications"
        pubs_params = {
            'limit': max_pubs,
            'offset': 0,
            'searchPersonEfrsbMessage': 'true',
            'searchPersonBankruptMessage': 'true',
            'searchAmReport': 'true'
        }
        
        session.headers['Referer'] = f"{FEDRESURS_URL}/{endpoint}/{guid}"
        resp_pubs = session.get(pubs_url, params=pubs_params, timeout=15)
        resp_pubs.raise_for_status()
        pubs_data = resp_pubs.json()
        
        total_pubs = pubs_data.get('total', 0)
        publications = pubs_data.get('pageData', [])
        
        return {
            'success': True,
            'inn': inn,
            'guid': guid,
            'name': name,
            'full_name': full_name,
            'endpoint': endpoint,
            'total_pubs': total_pubs,
            'publications': publications,
            'url': f"{FEDRESURS_URL}/{endpoint}/{guid}"
        }
        
    except requests.Timeout:
        logger.error(f"Timeout для ИНН {inn}")
        return {
            'success': False,
            'message': f"⏱ Превышено время ожидания\n\nСервер ЕФРСБ не отвечает. Попробуйте позже"
        }
    except requests.RequestException as e:
        logger.error(f"Ошибка запроса для ИНН {inn}: {e}")
        return {
            'success': False,
            'message': f"💥 Ошибка соединения с сервером\n\nПопробуйте повторить запрос"
        }
    except Exception as e:
        logger.error(f"Неожиданная ошибка для ИНН {inn}: {e}")
        return {
            'success': False,
            'message': f"💥 Произошла ошибка при обработке\n\nОбратитесь к администратору"
        }

def format_result(data, show_details=True):
    """Форматирование результата поиска"""
    if not data['success']:
        return data['message']
    
    entity_type = "🏢 Юридическое лицо" if data['endpoint'] == 'companies' else "👤 Физическое лицо"
    
    result = f"*{data['name']}*\n"
    result += f"{entity_type}\n"
    result += f"📋 ИНН: `{data['inn']}`\n"
    result += f"📊 Публикаций в ЕФРСБ: *{data['total_pubs']}*\n"
    result += f"🔗 [Открыть на Fedresurs]({data['url']})\n"
    
    if data['total_pubs'] == 0:
        result += f"\n✅ *Публикаций о банкротстве не найдено*"
        return result
    
    if show_details and data['publications']:
        result += f"\n━━━━━━━━━━━━━━━━━━\n"
        result += f"📄 *Последние публикации:*\n\n"
        
        for i, pub in enumerate(data['publications'], 1):
            number = pub.get('number', 'Без номера')
            type_name = pub.get('typeName', pub.get('type', 'Не указан тип'))
            date = pub.get('datePublish', '')[:10] if pub.get('datePublish') else 'Нет даты'
            
            result += f"*{i}.* `{number}`\n"
            result += f"   📌 {type_name}\n"
            result += f"   📅 {date}\n\n"
        
        if data['total_pubs'] > len(data['publications']):
            remain = data['total_pubs'] - len(data['publications'])
            result += f"_... и ещё {remain} публикаций_\n"
    
    return result[:4096]  # Telegram limit

# ============== KEYBOARDS ==============
def get_main_menu():
    """Главное меню"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔍 Поиск по ИНН", callback_data="search"),
        InlineKeyboardButton("⭐️ Избранное", callback_data="favorites")
    )
    markup.add(
        InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
        InlineKeyboardButton("ℹ️ Справка", callback_data="help")
    )
    return markup

def get_back_button():
    """Кнопка возврата"""
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("◀️ Назад", callback_data="menu"))
    return markup

def get_favorites_menu(inns):
    """Меню избранного"""
    markup = InlineKeyboardMarkup(row_width=1)
    
    for inn, name in inns:
        display_name = f"{name[:25]}..." if len(name) > 25 else name
        button_text = f"{display_name} ({inn})" if name else inn
        markup.add(InlineKeyboardButton(
            button_text, 
            callback_data=f"fav_search:{inn}"
        ))
    
    markup.add(
        InlineKeyboardButton("🗑 Очистить избранное", callback_data="clear_favorites"),
        InlineKeyboardButton("◀️ Главное меню", callback_data="menu")
    )
    return markup

def get_result_menu(inn, in_favorites=False):
    """Меню под результатом"""
    markup = InlineKeyboardMarkup(row_width=2)
    
    if in_favorites:
        markup.add(InlineKeyboardButton("❌ Удалить из избранного", 
                                       callback_data=f"del_fav:{inn}"))
    else:
        markup.add(InlineKeyboardButton("⭐️ В избранное", 
                                       callback_data=f"add_fav:{inn}"))
    
    markup.add(
        InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh:{inn}"),
        InlineKeyboardButton("◀️ Главное меню", callback_data="menu")
    )
    return markup

def get_settings_menu(user_id):
    """Меню настроек"""
    settings = get_user_settings(user_id)
    markup = InlineKeyboardMarkup(row_width=1)
    
    detail_status = "✅" if settings['show_details'] else "❌"
    markup.add(
        InlineKeyboardButton(
            f"{detail_status} Показывать детали публикаций",
            callback_data="toggle_details"
        )
    )
    
    markup.add(
        InlineKeyboardButton(
            f"📊 Показывать публикаций: {settings['max_pubs']}",
            callback_data="change_max_pubs"
        )
    )
    
    markup.add(InlineKeyboardButton("◀️ Главное меню", callback_data="menu"))
    return markup

def get_max_pubs_menu():
    """Меню выбора количества публикаций"""
    markup = InlineKeyboardMarkup(row_width=3)
    for num in [3, 5, 10]:
        markup.add(InlineKeyboardButton(str(num), callback_data=f"set_pubs:{num}"))
    markup.add(InlineKeyboardButton("◀️ Назад", callback_data="settings"))
    return markup

# ============== HANDLERS ==============
@bot.message_handler(commands=['start'])
def start(message):
    """Стартовое сообщение"""
    welcome_text = (
        "🔍 *Fedresurs Bot*\n\n"
        "Проверка статуса банкротства физических и юридических лиц "
        "по базе ЕФРСБ (Единый федеральный реестр сведений о банкротстве)\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "💡 *Как пользоваться:*\n\n"
        "1️⃣ Используйте inline-режим: `@botname ИНН`\n"
        "2️⃣ Или просто отправьте ИНН в чат\n"
        "3️⃣ Сохраняйте ИНН в избранное для быстрого доступа\n\n"
        "📝 *Формат ИНН:*\n"
        "• 10 цифр — юридическое лицо\n"
        "• 12 цифр — физическое лицо\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите действие:"
    )
    bot.send_message(
        message.chat.id,
        welcome_text,
        parse_mode='Markdown',
        reply_markup=get_main_menu()
    )
    logger.info(f"Пользователь {message.from_user.id} запустил бота")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Обработка текстовых сообщений"""
    inn = re.sub(r'\D', '', message.text)[:12]
    
    if len(inn) in [10, 12]:
        # Это похоже на ИНН
        bot.send_chat_action(message.chat.id, 'typing')
        msg = bot.send_message(message.chat.id, "🔍 Ищу информацию...")
        
        settings = get_user_settings(message.from_user.id)
        data = parse_bankrot(inn, settings['max_pubs'])
        result = format_result(data, settings['show_details'])
        
        # Проверяем, есть ли в избранном
        user_inns = get_user_inns(message.from_user.id)
        in_favorites = any(saved_inn == inn for saved_inn, _ in user_inns)
        
        bot.edit_message_text(
            result,
            message.chat.id,
            msg.message_id,
            parse_mode='Markdown',
            reply_markup=get_result_menu(inn, in_favorites),
            disable_web_page_preview=True
        )
        
        logger.info(f"Пользователь {message.from_user.id} запросил ИНН {inn}")
    else:
        # Неверный формат
        bot.send_message(
            message.chat.id,
            "❌ Неверный формат\n\n"
            "Отправьте ИНН (10 или 12 цифр)\n"
            "Или используйте меню:",
            reply_markup=get_main_menu()
        )

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """Обработка нажатий на кнопки"""
    user_id = call.from_user.id
    data = call.data
    
    try:
        # Главное меню
        if data == "menu":
            bot.edit_message_text(
                "🏠 *Главное меню*\n\nВыберите действие:",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=get_main_menu()
            )
            bot.answer_callback_query(call.id)
        
        # Поиск
        elif data == "search":
            bot.edit_message_text(
                "🔍 *Поиск по ИНН*\n\n"
                "Отправьте ИНН для поиска:\n"
                "• 10 цифр — юридическое лицо\n"
                "• 12 цифр — физическое лицо\n\n"
                "Пример: `7707083893` или `340735628010`",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=get_back_button()
            )
            bot.answer_callback_query(call.id, "Отправьте ИНН")
        
        # Избранное
        elif data == "favorites":
            inns = get_user_inns(user_id)
            if not inns:
                bot.edit_message_text(
                    "⭐️ *Избранное*\n\n"
                    "У вас пока нет сохранённых ИНН\n\n"
                    "💡 Чтобы добавить ИНН в избранное:\n"
                    "1. Найдите нужный ИНН\n"
                    "2. Нажмите кнопку \"⭐️ В избранное\"",
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode='Markdown',
                    reply_markup=get_back_button()
                )
            else:
                bot.edit_message_text(
                    f"⭐️ *Избранное*\n\n"
                    f"Сохранено ИНН: {len(inns)}\n"
                    f"Выберите для поиска:",
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode='Markdown',
                    reply_markup=get_favorites_menu(inns)
                )
            bot.answer_callback_query(call.id)
        
        # Поиск из избранного
        elif data.startswith("fav_search:"):
            inn = data.split(":")[1]
            bot.answer_callback_query(call.id, "🔍 Поиск...")
            bot.edit_message_text(
                "🔍 Загрузка данных...",
                call.message.chat.id,
                call.message.message_id
            )
            
            settings = get_user_settings(user_id)
            result_data = parse_bankrot(inn, settings['max_pubs'])
            result = format_result(result_data, settings['show_details'])
            
            bot.edit_message_text(
                result,
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=get_result_menu(inn, True),
                disable_web_page_preview=True
            )
        
        # Добавить в избранное
        elif data.startswith("add_fav:"):
            inn = data.split(":")[1]
            # Получаем имя из последнего поиска
            result_data = parse_bankrot(inn, 1)
            name = result_data.get('name', '') if result_data['success'] else ''
            
            if add_inn(user_id, inn, name):
                bot.answer_callback_query(call.id, "⭐️ Добавлено в избранное")
                # Обновляем кнопки
                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=get_result_menu(inn, True)
                )
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка сохранения")
        
        # Удалить из избранного
        elif data.startswith("del_fav:"):
            inn = data.split(":")[1]
            if delete_inn(user_id, inn):
                bot.answer_callback_query(call.id, "❌ Удалено из избранного")
                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=get_result_menu(inn, False)
                )
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка удаления")
        
        # Очистить избранное
        elif data == "clear_favorites":
            with get_db() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM user_inns WHERE user_id = ?", (user_id,))
                conn.commit()
            bot.answer_callback_query(call.id, "🗑 Избранное очищено")
            bot.edit_message_text(
                "⭐️ *Избранное*\n\n"
                "Все ИНН удалены",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=get_back_button()
            )
        
        # Обновить результат
        elif data.startswith("refresh:"):
            inn = data.split(":")[1]
            bot.answer_callback_query(call.id, "🔄 Обновление...")
            
            settings = get_user_settings(user_id)
            result_data = parse_bankrot(inn, settings['max_pubs'])
            result = format_result(result_data, settings['show_details'])
            
            user_inns = get_user_inns(user_id)
            in_favorites = any(saved_inn == inn for saved_inn, _ in user_inns)
            
            bot.edit_message_text(
                result,
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=get_result_menu(inn, in_favorites),
                disable_web_page_preview=True
            )
        
        # Настройки
        elif data == "settings":
            bot.edit_message_text(
                "⚙️ *Настройки*\n\n"
                "Настройте отображение результатов поиска:",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=get_settings_menu(user_id)
            )
            bot.answer_callback_query(call.id)
        
        # Переключить детали
        elif data == "toggle_details":
            settings = get_user_settings(user_id)
            new_value = not settings['show_details']
            update_user_settings(user_id, show_details=int(new_value), 
                               max_pubs=settings['max_pubs'])
            bot.answer_callback_query(
                call.id, 
                f"✅ Детали {'включены' if new_value else 'выключены'}"
            )
            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=get_settings_menu(user_id)
            )
        
        # Изменить количество публикаций
        elif data == "change_max_pubs":
            bot.edit_message_text(
                "⚙️ *Количество публикаций*\n\n"
                "Сколько публикаций показывать в результатах?",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=get_max_pubs_menu()
            )
            bot.answer_callback_query(call.id)
        
        # Установить количество публикаций
        elif data.startswith("set_pubs:"):
            num = int(data.split(":")[1])
            settings = get_user_settings(user_id)
            update_user_settings(user_id, show_details=int(settings['show_details']), 
                               max_pubs=num)
            bot.answer_callback_query(call.id, f"✅ Установлено: {num}")
            bot.edit_message_text(
                "⚙️ *Настройки*\n\n"
                "Настройте отображение результатов поиска:",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=get_settings_menu(user_id)
            )
        
        # Справка
        elif data == "help":
            help_text = (
                "ℹ️ *Справка*\n\n"
                "*Что такое ЕФРСБ?*\n"
                "Единый федеральный реестр сведений о банкротстве — официальная база данных "
                "о процедурах банкротства в России\n\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "*Способы поиска:*\n\n"
                "1️⃣ *Inline-режим*\n"
                "Наберите в любом чате:\n"
                "`@botname ИНН`\n\n"
                "2️⃣ *Прямой поиск*\n"
                "Отправьте ИНН боту\n\n"
                "3️⃣ *Избранное*\n"
                "Сохраняйте нужные ИНН для быстрого доступа\n\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "*Формат ИНН:*\n"
                "• 10 цифр — юридическое лицо\n"
                "• 12 цифр — физическое лицо\n\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "*Источник данных:*\n"
                "[fedresurs.ru](https://fedresurs.ru)"
            )
            bot.edit_message_text(
                help_text,
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=get_back_button(),
                disable_web_page_preview=True
            )
            bot.answer_callback_query(call.id)
        
        else:
            bot.answer_callback_query(call.id, "❌ Неизвестная команда")
    
    except Exception as e:
        logger.error(f"Ошибка обработки callback {data}: {e}")
        bot.answer_callback_query(call.id, "❌ Произошла ошибка")

# ============== INLINE MODE ==============
@bot.inline_handler(lambda query: bool(query.query))
def inline_query(query):
    """Обработка inline-запросов"""
    inn = re.sub(r'\D', '', query.query)[:12]
    
    if len(inn) not in [10, 12]:
        r = InlineQueryResultArticle(
            id="error",
            title="❌ Неверный формат ИНН",
            description="Введите 10 цифр (юрлицо) или 12 (физлицо)",
            input_message_content=InputTextMessageContent(
                "❌ Неверный формат ИНН\n\n"
                "Должен быть 10 цифр (юридическое лицо) или 12 цифр (физическое лицо)",
                parse_mode='Markdown'
            )
        )
        bot.answer_inline_query(query.id, [r], cache_time=1)
        return
    
    # Выполняем поиск
    data = parse_bankrot(inn, 5)
    result_text = format_result(data, True)
    
    if data['success']:
        title = f"✅ {data['name'][:40]}"
        description = f"ИНН {inn} • Публикаций: {data['total_pubs']}"
    else:
        title = f"❌ ИНН {inn}"
        description = "Не найден в базе ЕФРСБ"
    
    r = InlineQueryResultArticle(
        id=inn,
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            result_text,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    )
    
    bot.answer_inline_query(query.id, [r], cache_time=300)
    logger.info(f"Inline запрос от {query.from_user.id}: {inn}")

@bot.inline_handler(func=lambda query: not query.query)
def inline_empty(query):
    """Пустой inline-запрос"""
    r = InlineQueryResultArticle(
        id="help",
        title="🔍 Fedresurs Bot",
        description="Введите ИНН для поиска (10 или 12 цифр)",
        input_message_content=InputTextMessageContent(
            "💡 *Как использовать:*\n\n"
            "Наберите `@botname ИНН` в любом чате\n\n"
            "Примеры:\n"
            "• `@botname 7707083893`\n"
            "• `@botname 340735628010`",
            parse_mode='Markdown'
        )
    )
    bot.answer_inline_query(query.id, [r], cache_time=300)

# ============== MAIN ==============
if __name__ == '__main__':
    try:
        init_db()
        logger.info("🚀 Fedresurs Bot запущен")
        logger.info(f"📊 Режим: {os.getenv('BOT_TOKEN')[:10]}...")
        bot.infinity_polling(timeout=30, long_polling_timeout=30)
    except KeyboardInterrupt:
        logger.info("⛔️ Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
