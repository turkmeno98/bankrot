import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackData
from aiogram.client.default import DefaultBotProperties
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv('BOT_TOKEN')  # ← ENV-переменная!

if not TOKEN:
    print('❌ BOT_TOKEN не найден! export BOT_TOKEN="..."')
    exit(1)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
dp = Dispatcher()
cb = CallbackData('select', 'index', 'query')

options = uc.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
options.add_argument('--disable-gpu')

@dp.message(Command('search'))
async def search_handler(message: types.Message):
    query = message.text.removeprefix('/search ').strip().replace(' ', '%20')
    url = f'https://fedresurs.ru/entities?searchString={query}&regionNumber=all&isActive=true&offset=0&limit=15'
    
    await message.reply('🔍 Поиск...')
    
    driver = uc.Chrome(options=options)
    driver.get(url)
    
    try:
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'app-entity-search-result-card-person')))
        
        cards = driver.find_elements(By.CSS_SELECTOR, 'app-entity-search-result-card-person > div > div')
        persons = []
        
        for i, card in enumerate(cards[:10]):
            try:
                fio_el = card.find_element(By.CSS_SELECTOR, 'a, h3, .name, [title*="ФИО"], div')
                fio = fio_el.text.strip()[:50] if fio_el else 'N/A'
                inn_el = card.find_element(By.CSS_SELECTOR, '.inn, .tax-id, [title*="ИНН"]')
                inn = inn_el.text.strip() if inn_el else 'N/A'
                persons.append(f'{i+1}. {fio} | ИНН: {inn}')
            except: pass
        
        if persons:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=text.split('.')[0], callback_data=cb.new(index=str(i), query=query))]
                for i, text in enumerate(persons)
            ])
            await message.reply('\n'.join(persons), reply_markup=kb)
        else:
            await message.reply('❌ Не найдено.')
    except Exception as e:
        await message.reply(f'❌ {str(e)}')
    finally:
        driver.quit()

@dp.callback_query(cb.filter())
async def select_person(callback: types.CallbackQuery, callback_data: CallbackData):
    await callback.message.reply(f'📋 Детали #{callback_data.index}: {callback_data.query}')
    await callback.answer()

if __name__ == '__main__':
    print('🚀 Запуск!')
    asyncio.run(dp.start_polling(bot))
