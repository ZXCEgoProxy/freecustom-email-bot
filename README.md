# FreeCustom Email Manager Telegram Bot

Telegram-бот для управления временными почтовыми ящиками через API сервиса FreeCustom.Email.

## 🚀 Быстрый старт

### Локальная разработка

#### 1. Установка зависимостей

```bash
pip install -r requirements.txt
```

#### 2. Настройка

Создайте файл `.env` в корне проекта:

```env
BOT_TOKEN=your_telegram_bot_token_here
API_BASE_URL=https://api2.freecustom.email
DATABASE_PATH=database.db
EMAIL_CHECK_INTERVAL=30
DEADLINE_CHECK_INTERVAL=60
DEADLINE_WARNING_MINUTES=5
```

#### 3. Получение токена бота

1. Напишите [@BotFather](https://t.me/botfather) в Telegram
2. Создайте нового бота командой `/newbot`
3. Скопируйте токен и вставьте в `.env`

#### 4. Запуск бота

```bash
python bot.py
```

### 🚂 Деплой на Railway

#### 1. Создание репозитория на GitHub

Проект уже подготовлен для GitHub. Создайте новый репозиторий и загрузите код:

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/yourusername/freecustom-email-bot.git
git push -u origin main
```

#### 2. Деплой на Railway с PostgreSQL

1. Перейдите на [Railway.app](https://railway.app) и авторизуйтесь
2. Нажмите "New Project" → "Deploy from GitHub repo"
3. Выберите ваш репозиторий
4. Railway автоматически обнаружит Python приложение и предложит добавить PostgreSQL

#### 3. Добавление PostgreSQL

При создании проекта Railway предложит добавить сервисы:
- Выберите **PostgreSQL** как дополнительный сервис
- Railway автоматически свяжет PostgreSQL с вашим приложением
- Переменная `DATABASE_URL` будет автоматически добавлена

#### 4. Настройка переменных окружения в Railway

**ОБЯЗАТЕЛЬНО:** В панели управления проектом перейдите в раздел "Variables" и добавьте:

- `BOT_TOKEN` - **ОБЯЗАТЕЛЬНО** ваш токен Telegram бота от @BotFather

Railway автоматически добавит:
- `DATABASE_URL` - URL для подключения к PostgreSQL

Опциональные переменные (можно не указывать, будут использоваться значения по умолчанию):

- `API_BASE_URL` - `https://api2.freecustom.email`
- `EMAIL_CHECK_INTERVAL` - `30`
- `DEADLINE_CHECK_INTERVAL` - `60`
- `DEADLINE_WARNING_MINUTES` - `5`

**Важно:** Railway автоматически переключается на PostgreSQL при наличии `DATABASE_URL`!

#### 4. Запуск

Railway автоматически запустит бота после настройки переменных. Проверьте логи в панели управления для отладки.

## 📋 Функциональность

- ✅ Настройка API ключа FreeCustom.Email
- ✅ Создание временных почтовых ящиков
- ✅ Просмотр списка активных ящиков
- ✅ Чтение входящих писем
- ✅ Автоматическое извлечение OTP кодов
- ✅ Удаление почтовых ящиков
- ✅ Мониторинг истечения срока действия
- ✅ Защита от спама (rate limiting)

## 🏗 Архитектура

```
bot.py          # Основной файл бота
config.py       # Конфигурация и настройки
database.py     # Работа с SQLite базой данных
api_client.py   # Клиент для FreeCustom.Email API
requirements.txt # Зависимости Python
.env            # Конфигурационные переменные
```

## 🗄 База данных

Бот поддерживает две базы данных:

### Локальная разработка (SQLite)
- Используется файловая база данных SQLite
- Автоматически включается при отсутствии `DATABASE_URL`

### Продакшн (PostgreSQL)
- Railway автоматически предоставляет PostgreSQL
- Включается при наличии переменной `DATABASE_URL`
- Более надежная и масштабируемая

### Структура данных
Обе базы используют одинаковую структуру с тремя таблицами:

- `users` - пользователи и их API ключи
- `inboxes` - почтовые ящики пользователей
- `messages` - кэшированные письма

## 🔧 API FreeCustom.Email

Бот использует следующие эндпоинты:

- `GET /domains` - список доступных доменов
- `POST /emails` - создание нового ящика
- `GET /emails/{email}/messages` - получение писем
- `DELETE /emails/{email}` - удаление ящика

## 🎯 Использование

1. Запустите бота командой `/start`
2. Настройте API ключ от FreeCustom.Email
3. Создавайте почтовые ящики через меню
4. Используйте адреса для регистрации на сайтах
5. Проверяйте входящие письма через бота

## 🔒 Безопасность

- API ключи хранятся в зашифрованном виде
- Rate limiting предотвращает злоупотребления
- Проверка валидности API ключей перед сохранением
- Автоматическая очистка данных при удалении аккаунта

## 📝 Разработка

### Запуск тестов

```bash
python test.py
```

### Структура кода

Бот построен на aiogram 3.x с использованием:

- FSM для управления состояниями
- Inline клавиатуры для навигации
- APScheduler для фоновых задач
- AIOHTTP для API запросов

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## 📄 Лицензия

MIT License - см. файл LICENSE для деталей.

## 🆘 Поддержка

Если возникли проблемы:

1. Проверьте корректность API ключа
2. Убедитесь в доступности сервиса freecustom.email
3. Проверьте логи бота на ошибки
4. Создайте issue в репозитории

## 🔄 Обновления

- Регулярно обновляйте зависимости
- Следите за изменениями в API FreeCustom.Email
- Проверяйте совместимость с новыми версиями aiogram