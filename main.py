import os
import sqlite3
import requests
import re
import logging
from bs4 import BeautifulSoup
from telebot import TeleBot
from telebot.types import (InlineKeyboardMarkup, InlineKeyboardButton, 
                          InlineQueryResultArticle, InputTextMessageContent)
from datetime import datetime
from contextlib import contextmanager
import time

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Инициализация
bot = TeleBot(os.getenv('BOT_TOKEN'))
FEDRESURS_URL = "https://fedresurs.ru"
FEDRESURS_LOGIN = os.getenv('FEDRESURS_LOGIN', '')  # Ваш логин
FEDRESURS_PASSWORD = os.getenv('FEDRESURS_PASSWORD', '')  # Ваш пароль
DB_PATH = 'inns.db'

# Настройки сессии
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Cache-Control': 'max-age=0'
})

# ============== AUTH ==============
def login_fedresurs():
    """Авторизация на Fedresurs"""
    if not FEDRESURS_LOGIN or not FEDRESURS_PASSWORD:
        logger.info("Логин/пароль не указаны, работаем без авторизации")
        return True
    
    try:
        logger.info("Попытка авторизации на Fedresurs...")
        
        # Шаг 1: Получаем страницу входа
        login_page_url = f"{FEDRESURS_URL}/login"
        resp = session.get(login_page_url, timeout=15)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Ищем форму входа и CSRF токен (если есть)
        csrf_token = None
        csrf_input = soup.find('input', {'name': re.compile(r'csrf|token|_token', re.IGNORECASE)})
        if csrf_input:
            csrf_token = csrf_input.get('value')
            logger.info(f"Найден CSRF токен")
        
        # Шаг 2: Отправляем данные авторизации
        login_data = {
            'username': FEDRESURS_LOGIN,
            'password': FEDRESURS_PASSWORD,
            'login': 'Войти'
        }
        
        if csrf_token:
            # Пробуем разные варианты имени поля для CSRF
            for csrf_field in ['_csrf', 'csrf_token', '_token', 'authenticity_token']:
                login_data[csrf_field] = csrf_token
        
        # Находим URL для отправки формы
        form = soup.find('form')
        if form:
            action = form.get('action', '/login')
            login_url = action if action.startswith('http') else FEDRESURS_URL + action
        else:
            login_url = login_page_url
        
        logger.info(f"Отправка данных авторизации на {login_url}")
        
        resp = session.post(
            login_url,
            data=login_data,
            timeout=15,
            allow_redirects=True
        )
        resp.raise_for_status()
        
        # Проверяем, успешна ли авторизация
        if 'logout' in resp.text.lower() or 'выход' in resp.text.lower():
            logger.info("✅ Авторизация успешна")
            return True
        else:
            logger.warning("⚠️ Авторизация не удалась, работаем без авторизации")
            return False
            
    except Exception as e:
        logger.error(f"Ошибка авторизации: {e}")
        return False

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

# ============== FEDRESURS PARSER ==============
def search_by_inn(inn):
    """Поиск по ИНН через страницу поиска"""
    try:
        inn = re.sub(r'\D', '', inn)[:12]
        
        if len(inn) not in [10, 12]:
            return None
        
        # Формируем правильный URL для поиска
        search_url = f"{FEDRESURS_URL}/entities"
        params = {
            'searchString': inn,
            'regionNumber': 'all',
            'isActive': 'true',
            'offset': 0,
            'limit': 15
        }
        
        logger.info(f"Поиск ИНН {inn}: {search_url}?searchString={inn}")
        
        # Делаем запрос с retry
        max_retries = 3
        for attempt in range(max_retries):
            try:
                time.sleep(0.5 * (attempt + 1))  # Увеличиваем задержку при повторах
                
                resp = session.get(search_url, params=params, timeout=20)
                resp.raise_for_status()
                
                logger.info(f"Получен ответ: статус {resp.status_code}, длина {len(resp.text)} байт")
                
                # Сохраняем HTML для отладки
                with open(f'search_{inn}.html', 'w', encoding='utf-8') as f:
                    f.write(resp.text)
                logger.info(f"HTML сохранён в search_{inn}.html")
                
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                # Ищем все возможные варианты ссылок на профиль
                profile_links = []
                
                # Вариант 1: Прямые ссылки
                for link in soup.find_all('a', href=True):
                    href = link.get('href', '')
                    if '/entities/' in href:
                        profile_links.append(href)
                        logger.info(f"Найдена ссылка: {href}")
                
                if profile_links:
                    profile_path = profile_links[0]
                    profile_url = profile_path if profile_path.startswith('http') else FEDRESURS_URL + profile_path
                    logger.info(f"✅ Найден профиль: {profile_url}")
                    return profile_url
                
                # Если не нашли, ищем ID в JavaScript или data-атрибутах
                page_text = resp.text
                
                # Паттерны для поиска ID
                patterns = [
                    r'/entities/([a-f0-9\-]{36})',  # UUID
                    r'entity[_-]?id["\s:=]+([a-f0-9\-]{36})',
                    r'"id"\s*:\s*"([a-f0-9\-]{36})"'
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, page_text, re.IGNORECASE)
                    if match:
                        entity_id = match.group(1)
                        profile_url = f"{FEDRESURS_URL}/entities/{entity_id}"
                        logger.info(f"✅ Найден ID из кода: {profile_url}")
                        return profile_url
                
                logger.warning(f"Ссылки не найдены (попытка {attempt + 1}/{max_retries})")
                
                if attempt < max_retries - 1:
                    continue
                else:
                    return None
                    
            except requests.Timeout:
                logger.error(f"Timeout при поиске (попытка {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    continue
                else:
                    raise
            except requests.ConnectionError as e:
                logger.error(f"Ошибка соединения (попытка {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                else:
                    raise
        
        return None
        
    except Exception as e:
        logger.error(f"Ошибка поиска ИНН {inn}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

def parse_profile(profile_url):
    """Парсинг страницы профиля"""
    try:
        time.sleep(0.5)
        resp = session.get(profile_url, timeout=20)
        resp.raise_for_status()
        
        # Сохраняем HTML для отладки
        entity_id = profile_url.split('/')[-1]
        with open(f'profile_{entity_id}.html', 'w', encoding='utf-8') as f:
            f.write(resp.text)
        logger.info(f"Профиль сохранён в profile_{entity_id}.html")
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        data = {
            'url': profile_url,
            'name': None,
            'inn': None,
            'type': 'persons' if '/persons/' in profile_url else 'companies',
            'publications': []
        }
        
        # Извлекаем название
        name_selectors = [
            ('h1', {}),
            ('div', {'class': 'entity-name'}),
            ('div', {'class': 'card-title'}),
            ('span', {'class': 'name'}),
        ]
        
        for tag, attrs in name_selectors:
            name_elem = soup.find(tag, attrs)
            if name_elem:
                data['name'] = name_elem.get_text(strip=True)
                logger.info(f"Название: {data['name']}")
                break
        
        # Извлекаем ИНН из текста страницы
        page_text = soup.get_text()
        inn_match = re.search(r'ИНН[:\s]*(\d{10,12})', page_text)
        if inn_match:
            data['inn'] = inn_match.group(1)
            logger.info(f"ИНН: {data['inn']}")
        
        # Извлекаем публикации (ищем все возможные варианты)
        pub_containers = soup.find_all(['div', 'tr', 'article'], limit=50)
        
        for item in pub_containers:
            item_text = item.get_text()
            
            # Ищем элементы с номерами публикаций
            if '№' in item_text and len(item_text) > 20:
                pub = {}
                
                # Номер
                num_match = re.search(r'№\s*(\d+)', item_text)
                pub['number'] = num_match.group(1) if num_match else 'Б/Н'
                
                # Дата
                date_match = re.search(r'(\d{2}\.\d{2}\.\d{4})', item_text)
                pub['date'] = date_match.group(1) if date_match else 'Нет даты'
                
                # Тип (берём первую строку текста без даты и номера)
                clean_text = item_text.replace(pub['number'], '').replace(pub['date'], '')
                lines = [l.strip() for l in clean_text.split('\n') if len(l.strip()) > 10]
                pub['type'] = lines[0][:80] if lines else 'Не указан'
                
                data['publications'].append(pub)
                
                if len(data['publications']) >= 10:
                    break
        
        # Общее количество
        total_match = re.search(r'(Всего|Найдено|публикаций)[:\s]*(\d+)', page_text, re.IGNORECASE)
        data['total_pubs'] = int(total_match.group(2)) if total_match else len(data['publications'])
        
        logger.info(f"Найдено публикаций: {len(data['publications'])} (всего: {data['total_pubs']})")
        return data
        
    except Exception as e:
        logger.error(f"Ошибка парсинга профиля {profile_url}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

def parse_bankrot(inn, max_pubs=5):
    """Основная функция парсинга"""
    try:
        inn = re.sub(r'\D', '', inn)[:12]
        
        if len(inn) not in [10, 12]:
            return {
                'success': False,
                'message': f"❌ Неверный формат ИНН\n\nДолжен быть 10 цифр (юрлицо) или 12 (физлицо)\nВы ввели: `{inn}` ({len(inn)} цифр)"
            }
        
        # Шаг 1: Поиск профиля
        profile_url = search_by_inn(inn)
        if not profile_url:
            return {
                'success': False,
                'message': f"❌ ИНН `{inn}` не найден в ЕФРСБ\n\n💡 Возможные причины:\n• ИНН введён неверно\n• Данные отсутствуют в базе\n• Проблемы с сайтом fedresurs.ru\n\n🔍 Проверьте вручную:\n{FEDRESURS_URL}/entities?searchString={inn}"
            }
        
        # Шаг 2: Парсинг профиля
        data = parse_profile(profile_url)
        if not data:
            return {
                'success': False,
                'message': f"❌ Не удалось загрузить данные\n\nСтраница найдена, но структура не распознана.\nПроверьте файлы debug для анализа."
            }
        
        # Ограничиваем количество публикаций
        data['publications'] = data['publications'][:max_pubs]
        
        return {
            'success': True,
            'inn': data['inn'] or inn,
            'name': data['name'] or 'Не указано',
            'endpoint': data['type'],
            'total_pubs': data['total_pubs'],
            'publications': data['publications'],
            'url': profile_url
        }
        
    except Exception as e:
        logger.error(f"Неожиданная ошибка для ИНН {inn}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'success': False,
            'message': f"💥 Произошла ошибка при обработке\n\nПроверьте файл bot.log для деталей"
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
            result += f"*{i}.* `№{pub.get('number', 'Б/Н')}`\n"
            result += f"   📌 {pub.get('type', 'Не указан')[:50]}\n"
            result += f"   📅 {pub.get('date', 'Нет даты')}\n\n"
        
        if data['total_pubs'] > len(data['publications']):
            remain = data['total_pubs'] - len(data['publications'])
            result += f"_... и ещё {remain} публикаций_\n"
    
    return result[:4096]

# ============== KEYBOARDS (те же что и раньше) ==============
def get_main_menu():
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
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("◀️ Назад", callback_data="menu"))
    return markup

def get_favorites_menu(inns):
    markup = InlineKeyboardMarkup(row_width=1)
    for inn, name in inns:
        display_name = f"{name[:25]}..." if len(name) > 25 else name
        button_text = f"{display_name} ({inn})" if name else inn
        markup.add(InlineKeyboardButton(button_text, callback_data=f"fav_search:{inn}"))
    markup.add(
        InlineKeyboardButton("🗑 Очистить избранное", callback_data="clear_favorites"),
        InlineKeyboardButton("◀️ Главное меню", callback_data="menu")
    )
    return markup

def get_result_menu(inn, in_favorites=False):
    markup = InlineKeyboardMarkup(row_width=2)
    if in_favorites:
        markup.add(InlineKeyboardButton("❌ Удалить из избранного", callback_data=f"del_fav:{inn}"))
    else:
        markup.add(InlineKeyboardButton("⭐️ В избранное", callback_data=f"add_fav:{inn}"))
    markup.add(
        InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh:{inn}"),
        InlineKeyboardButton("◀️ Главное меню", callback_data="menu")
    )
    return markup

def get_settings_menu(user_id):
    settings = get_user_settings(user_id)
    markup = InlineKeyboardMarkup(row_width=1)
    detail_status = "✅" if settings['show_details'] else "❌"
    markup.add(
        InlineKeyboardButton(f"{detail_status} Показывать детали публикаций", callback_data="toggle_details")
    )
    markup.add(
        InlineKeyboardButton(f"📊 Показывать публикаций: {settings['max_pubs']}", callback_data="change_max_pubs")
    )
    markup.add(InlineKeyboardButton("◀️ Главное меню", callback_data="menu"))
    return markup

def get_max_pubs_menu():
    markup = InlineKeyboardMarkup(row_width=3)
    for num in [3, 5, 10]:
        markup.add(InlineKeyboardButton(str(num), callback_data=f"set_pubs:{num}"))
    markup.add(InlineKeyboardButton("◀️ Назад", callback_data="settings"))
    return markup

# ============== HANDLERS (сокращённо, остальное как раньше) ==============
@bot.message_handler(commands=['start'])
def start(message):
    welcome_text = (
        "🔍 *Fedresurs Parser Bot*\n\n"
        "Проверка статуса банкротства физических и юридических лиц "
        "через ЕФРСБ\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "💡 *Как пользоваться:*\n\n"
        "1️⃣ Используйте inline-режим: `@botname ИНН`\n"
        "2️⃣ Или просто отправьте ИНН в чат\n"
        "3️⃣ Сохраняйте ИНН в избранное\n\n"
        "📝 *Формат ИНН:*\n"
        "• 10 цифр — юридическое лицо\n"
        "• 12 цифр — физическое лицо\n\n"
        "Выберите действие:"
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=get_main_menu())

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    inn = re.sub(r'\D', '', message.text)[:12]
    if len(inn) in [10, 12]:
        bot.send_chat_action(message.chat.id, 'typing')
        msg = bot.send_message(message.chat.id, "🔍 Парсинг данных...\n_Это может занять до 30 секунд_", parse_mode='Markdown')
        
        settings = get_user_settings(message.from_user.id)
        data = parse_bankrot(inn, settings['max_pubs'])
        result = format_result(data, settings['show_details'])
        
        user_inns = get_user_inns(message.from_user.id)
        in_favorites = any(saved_inn == inn for saved_inn, _ in user_inns)
        
        bot.edit_message_text(result, message.chat.id, msg.message_id, parse_mode='Markdown',
                            reply_markup=get_result_menu(inn, in_favorites), disable_web_page_preview=True)
    else:
        bot.send_message(message.chat.id, "❌ Неверный формат\n\nОтправьте ИНН (10 или 12 цифр)", reply_markup=get_main_menu())

# [Остальные обработчики callback и inline такие же как в предыдущей версии]
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    # ... (код обработки callback из предыдущей версии)
    pass

@bot.inline_handler(lambda query: bool(query.query))
def inline_query(query):
    inn = re.sub(r'\D', '', query.query)[:12]
    if len(inn) not in [10, 12]:
        r = InlineQueryResultArticle(id="error", title="❌ Неверный формат ИНН",
            description="Введите 10 или 12 цифр",
            input_message_content=InputTextMessageContent("❌ Неверный формат ИНН", parse_mode='Markdown'))
        bot.answer_inline_query(query.id, [r], cache_time=1)
        return
    
    data = parse_bankrot(inn, 5)
    result_text = format_result(data, True)
    title = f"✅ {data['name'][:40]}" if data['success'] else f"❌ ИНН {inn}"
    description = f"ИНН {inn} • Публикаций: {data['total_pubs']}" if data['success'] else "Не найден"
    
    r = InlineQueryResultArticle(id=inn, title=title, description=description,
        input_message_content=InputTextMessageContent(result_text, parse_mode='Markdown', disable_web_page_preview=True))
    bot.answer_inline_query(query.id, [r], cache_time=300)

@bot.inline_handler(func=lambda query: not query.query)
def inline_empty(query):
    r = InlineQueryResultArticle(id="help", title="🔍 Fedresurs Parser Bot",
        description="Введите ИНН для поиска (10 или 12 цифр)",
        input_message_content=InputTextMessageContent("💡 Наберите `@botname ИНН`", parse_mode='Markdown'))
    bot.answer_inline_query(query.id, [r], cache_time=300)

# ============== MAIN ==============
if __name__ == '__main__':
    try:
        init_db()
        logger.info("🚀 Fedresurs Parser Bot запущен")
        
        # Пробуем авторизоваться
        if FEDRESURS_LOGIN and FEDRESURS_PASSWORD:
            login_fedresurs()
        
        logger.info(f"📄 Режим: HTML парсинг {'с авторизацией' if FEDRESURS_LOGIN else 'без авторизации'}")
        bot.infinity_polling(timeout=30, long_polling_timeout=30)
    except KeyboardInterrupt:
        logger.info("⛔️ Бот остановлен")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
        import traceback
        logger.error(traceback.format_exc())
