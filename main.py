import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackData
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
from undetected_chromedriver import ChromeOptions
import undetected_chromedriver as uc

logging.basicConfig(level=logging.INFO)
bot = TeleBot(os.getenv('BOT_TOKEN'))  # Замени!
dp = Dispatcher()
cb = CallbackData('select', 'index', 'query')

options = uc.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')

@dp.message(Command('search'))
async def search_handler(message: types.Message):
    query = message.text.removeprefix('/search ').strip().replace(' ', '%20')
    url = f'https://fedresurs.ru/entities?searchString={query}&regionNumber=all&isActive=true&offset=0&limit=15'
    
    try:
        driver = uc.Chrome(options=options)
        driver.get(url)
        
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'app-entity-search-result-card-person')))
        
        cards = driver.find_elements(By.CSS_SELECTOR, 'app-entity-search-result-card-person > div > div')
        persons = []
        
        for i, card in enumerate(cards[:10]):
            try:
                fio = card.find_element(By.CSS_SELECTOR, 'a, .name').text.strip() or 'N/A'
                inn = card.find_element(By.CSS_SELECTOR, '.inn, [title*="ИНН"]').text.strip() or 'N/A'
                status = card.find_element(By.CSS_SELECTOR, '.status').text.strip() or 'N/A'
                persons.append(f'{i+1}. {fio} | ИНН: {inn} | {status}')
            except: pass
        
        driver.quit()
        
        if persons:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=text, callback_data=cb.new(index=str(i), query=query))]
                for i, text in enumerate(persons)
            ])
            await message.reply('Выберите человека:', reply_markup=kb)
        else:
            await message.reply('Ничего не найдено.')
    except Exception as e:
        await message.reply(f'Ошибка: {str(e)}')

@dp.callback_query(cb.filter())
async def select_person(callback: types.CallbackQuery, callback_data: CallbackData):
    index = int(callback_data.index)
    query = callback_data.query
    
    # Здесь: повторно открываем поиск, кликаем на index-карточку, парсим детали
    await callback.message.reply(f'Парсинг деталей для #{index+1}... (добавлю в финал)')
    await callback.answer()

if __name__ == '__main__':
    asyncio.run(dp.start_polling(bot))
