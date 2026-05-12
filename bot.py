import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import Config
from database import db, init_database, close_database
from api_client import FreeCustomAPIClient, FreeCustomAPIError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables (initialized in main)
bot = None
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# FSM States
class APIKeySetup(StatesGroup):
    waiting_for_key = State()

class EmailManagement(StatesGroup):
    waiting_for_domain = State()

# Rate limiting storage
user_last_request: Dict[int, datetime] = {}
RATE_LIMIT_SECONDS = 1

# Helper functions
def check_rate_limit(user_id: int) -> bool:
    """Check if user is rate limited"""
    now = datetime.now()
    if user_id in user_last_request:
        time_diff = (now - user_last_request[user_id]).total_seconds()
        if time_diff < RATE_LIMIT_SECONDS:
            return False
    user_last_request[user_id] = now
    return True

def format_email_card(email_data: Dict[str, Any]) -> str:
    """Format email information as a card"""
    email = email_data['email']
    created_at = email_data['created_at']
    expires_at = email_data.get('expires_at')
    message_count = email_data.get('message_count', 0)

    text = f"📧 <b>{email}</b>\n"
    text += f"🕒 Создан: {created_at.strftime('%Y-%m-%d %H:%M')}\n"

    if expires_at:
        text += f"⏳ Живет до: {expires_at.strftime('%Y-%m-%d %H:%M')}\n"

    text += f"📨 Писем: {message_count}\n"

    return text

def format_message_preview(message: Dict[str, Any]) -> str:
    """Format message preview"""
    subject = message.get('subject', 'Без темы')
    sender = message.get('sender', 'Неизвестный')
    received_at = message.get('received_at')

    text = f"📩 <b>{subject}</b>\n"
    text += f"👤 От: {sender}\n"

    if received_at:
        if isinstance(received_at, str):
            text += f"🕒 {received_at}\n"
        else:
            text += f"🕒 {received_at.strftime('%Y-%m-%d %H:%M')}\n"

    return text

# Keyboard functions
def get_main_menu_keyboard() -> types.InlineKeyboardMarkup:
    """Main menu keyboard"""
    keyboard = [
        [types.InlineKeyboardButton(text="📬 Мои почты", callback_data="list_emails")],
        [types.InlineKeyboardButton(text="➕ Создать новую почту", callback_data="create_email")],
        [types.InlineKeyboardButton(text="⚙️ Настройки API", callback_data="api_settings")],
        [types.InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_email_actions_keyboard(inbox_id: int) -> types.InlineKeyboardMarkup:
    """Email actions keyboard"""
    keyboard = [
        [types.InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh_email:{inbox_id}")],
        [types.InlineKeyboardButton(text="📩 Читать", callback_data=f"read_email:{inbox_id}")],
        [types.InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_email:{inbox_id}")],
        [types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_list")]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_message_actions_keyboard(message_id: int, inbox_id: int) -> types.InlineKeyboardMarkup:
    """Message actions keyboard"""
    keyboard = [
        [types.InlineKeyboardButton(text="🤖 Извлечь OTP", callback_data=f"extract_otp:{message_id}")],
        [types.InlineKeyboardButton(text="🔙 К списку писем", callback_data=f"read_email:{inbox_id}")]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_api_settings_keyboard() -> types.InlineKeyboardMarkup:
    """API settings keyboard"""
    keyboard = [
        [types.InlineKeyboardButton(text="🔄 Сменить ключ", callback_data="change_api_key")],
        [types.InlineKeyboardButton(text="❌ Удалить ключ", callback_data="delete_api_key")],
        [types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_back_keyboard(callback_data: str = "back_to_main") -> types.InlineKeyboardMarkup:
    """Simple back button"""
    keyboard = [[types.InlineKeyboardButton(text="🔙 Назад", callback_data=callback_data)]]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

# Command handlers
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Handle /start command"""
    user_id = message.from_user.id

    # Check if user has API key
    user_data = await db.get_user(user_id)

    if user_data:
        # User has API key, show main menu
        await message.answer(
            "👋 Добро пожаловать в FreeCustom Email Manager!\n\n"
            "Выберите действие:",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        # User needs to set up API key
        await message.answer(
            "👋 Добро пожаловать в FreeCustom Email Manager!\n\n"
            "Для начала работы необходимо настроить API ключ от сервиса FreeCustom.Email.\n\n"
            "🔑 Получите ключ на сайте freecustom.email и отправьте его мне:",
            reply_markup=get_back_keyboard("cancel_setup")
        )
        await state.set_state(APIKeySetup.waiting_for_key)

@dp.message(APIKeySetup.waiting_for_key)
async def process_api_key(message: types.Message, state: FSMContext):
    """Process API key input"""
    user_id = message.from_user.id
    api_key = message.text.strip()

    if not api_key:
        await message.answer("❌ Ключ не может быть пустым. Попробуйте еще раз:")
        return

    # Validate API key
    try:
        logger.info(f"Validating API key for user {user_id}: {api_key[:10]}...")
        async with FreeCustomAPIClient(api_key) as client:
            is_valid = await client.validate_api_key()
            logger.info(f"API key validation result: {is_valid}")

            if is_valid:
                await db.save_user(user_id, api_key)
                await message.answer(
                    "✅ API ключ успешно сохранен!\n\n"
                    "Теперь вы можете управлять временными почтовыми ящиками.",
                    reply_markup=get_main_menu_keyboard()
                )
                await state.clear()
            else:
                await message.answer(
                    "❌ Неверный API ключ. Проверьте ключ и попробуйте еще раз:"
                )
    except FreeCustomAPIError as e:
        await message.answer(f"❌ Ошибка проверки ключа: {str(e)}\n\nПопробуйте еще раз:")

# Callback query handlers
@dp.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    """Back to main menu"""
    await callback.message.edit_text(
        "Выберите действие:",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "cancel_setup")
async def cancel_setup(callback: types.CallbackQuery, state: FSMContext):
    """Cancel API key setup"""
    await state.clear()
    await callback.message.edit_text(
        "Настройка отменена. Отправьте /start для повторной попытки."
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "list_emails")
async def list_emails(callback: types.CallbackQuery):
    """List user's emails"""
    user_id = callback.from_user.id

    if not check_rate_limit(user_id):
        await callback.answer("⏳ Подождите немного перед следующим запросом")
        return

    user_data = await db.get_user(user_id)
    if not user_data:
        await callback.message.edit_text(
            "❌ API ключ не найден. Необходимо настроить ключ заново.",
            reply_markup=get_back_keyboard("change_api_key")
        )
        await callback.answer()
        return

    inboxes = await db.get_user_inboxes(user_id)

    if not inboxes:
        await callback.message.edit_text(
            "📭 У вас пока нет активных почтовых ящиков.\n\n"
            "➕ Создайте новый ящик для начала работы.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="➕ Создать почту", callback_data="create_email")],
                [types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
            ])
        )
        await callback.answer()
        return

    # Get message counts for each inbox
    text = "📬 Ваши почтовые ящики:\n\n"

    keyboard_buttons = []
    for inbox in inboxes:
        inbox_id = inbox['id']
        messages = await db.get_inbox_messages(inbox_id)
        inbox['message_count'] = len(messages)

        text += format_email_card(inbox) + "\n"

        keyboard_buttons.append([
            types.InlineKeyboardButton(
                text=f"📧 {inbox['email']}",
                callback_data=f"manage_email:{inbox_id}"
            )
        ])

    keyboard_buttons.append([types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("manage_email:"))
async def manage_email(callback: types.CallbackQuery):
    """Manage specific email"""
    inbox_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    if not check_rate_limit(user_id):
        await callback.answer("⏳ Подождите немного перед следующим запросом")
        return

    inbox = await db.get_inbox(inbox_id)
    if not inbox or inbox['user_id'] != user_id:
        await callback.answer("❌ Почтовый ящик не найден")
        return

    messages = await db.get_inbox_messages(inbox_id)

    text = format_email_card({**inbox, 'message_count': len(messages)})
    text += "\nВыберите действие:"

    await callback.message.edit_text(
        text,
        reply_markup=get_email_actions_keyboard(inbox_id)
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "create_email")
async def create_email_start(callback: types.CallbackQuery, state: FSMContext):
    """Start email creation process"""
    user_id = callback.from_user.id

    user_data = await db.get_user(user_id)
    if not user_data:
        await callback.message.edit_text(
            "❌ API ключ не найден. Необходимо настроить ключ заново.",
            reply_markup=get_back_keyboard("change_api_key")
        )
        await callback.answer()
        return

    # Try to create email directly
    try:
        logger.info(f"Creating email for user {user_id} with API key: {user_data['api_key'][:10]}...")
        async with FreeCustomAPIClient(user_data['api_key']) as client:
            # First validate the API key is still working
            is_valid = await client.validate_api_key()
            if not is_valid:
                logger.error(f"API key validation failed for user {user_id}")
                await callback.message.edit_text(
                    "❌ API ключ больше не действителен. Пожалуйста, обновите ключ в настройках.",
                    reply_markup=get_back_keyboard("change_api_key")
                )
                await callback.answer()
                return

            email_data = await client.create_email()
            logger.info(f"Email created successfully: {email_data}")

            # Save to database
            expires_at = FreeCustomAPIClient.parse_expiry_time(email_data.get('expires_in'))
            inbox_id = await db.save_inbox(user_id, email_data['email'], expires_at)

            # Copy address button
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="📋 Копировать адрес", callback_data=f"copy_email:{inbox_id}")],
                [types.InlineKeyboardButton(text="⚙️ Управление", callback_data=f"manage_email:{inbox_id}")],
                [types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
            ])

            await callback.message.edit_text(
                f"✅ Почтовый ящик успешно создан!\n\n"
                f"📧 <b>{email_data['email']}</b>\n\n"
                f"Используйте этот адрес для регистрации на нужных сайтах.",
                reply_markup=keyboard
            )

    except FreeCustomAPIError as e:
        logger.error(f"Failed to create email for user {user_id}: {str(e)}")
        await callback.message.edit_text(
            f"❌ Ошибка создания почты: {str(e)}\n\n"
            "Попробуйте еще раз или проверьте API ключ.",
            reply_markup=get_back_keyboard("back_to_main")
        )

    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("copy_email:"))
async def copy_email(callback: types.CallbackQuery):
    """Copy email address"""
    inbox_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    inbox = await db.get_inbox(inbox_id)
    if not inbox or inbox['user_id'] != user_id:
        await callback.answer("❌ Почтовый ящик не найден")
        return

    await callback.message.answer(
        f"📧 Скопируйте адрес:\n\n<code>{inbox['email']}</code>",
        parse_mode=ParseMode.HTML
    )
    await callback.answer("Адрес скопирован!")

@dp.callback_query(lambda c: c.data.startswith("refresh_email:"))
async def refresh_email(callback: types.CallbackQuery):
    """Refresh email messages"""
    inbox_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    if not check_rate_limit(user_id):
        await callback.answer("⏳ Подождите немного перед следующим запросом")
        return

    inbox = await db.get_inbox(inbox_id)
    if not inbox or inbox['user_id'] != user_id:
        await callback.answer("❌ Почтовый ящик не найден")
        return

    user_data = await db.get_user(user_id)
    if not user_data:
        await callback.answer("❌ API ключ не найден")
        return

    try:
        async with FreeCustomAPIClient(user_data['api_key']) as client:
            messages_data = await client.get_email_messages(inbox['email'])

            # Save new messages
            for msg_data in messages_data:
                await db.save_message(inbox_id, msg_data)

            await db.update_inbox_last_checked(inbox_id)

            messages = await db.get_inbox_messages(inbox_id)

            if messages:
                await callback.answer(f"✅ Обновлено! Найдено {len(messages)} писем")
            else:
                await callback.answer("✅ Обновлено. Новых писем нет.")

            # Refresh the manage view
            await manage_email(callback)

    except FreeCustomAPIError as e:
        await callback.answer(f"❌ Ошибка обновления: {str(e)}")

@dp.callback_query(lambda c: c.data.startswith("read_email:"))
async def read_email(callback: types.CallbackQuery):
    """Read email messages"""
    inbox_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    inbox = await db.get_inbox(inbox_id)
    if not inbox or inbox['user_id'] != user_id:
        await callback.answer("❌ Почтовый ящик не найден")
        return

    messages = await db.get_inbox_messages(inbox_id)

    if not messages:
        await callback.message.edit_text(
            f"📭 В ящике <b>{inbox['email']}</b> пока нет писем.\n\n"
            "Нажмите '🔄 Обновить' для проверки новых писем.",
            reply_markup=get_email_actions_keyboard(inbox_id)
        )
        await callback.answer()
        return

    text = f"📨 Письма в ящике <b>{inbox['email']}</b>:\n\n"

    keyboard_buttons = []
    for msg in messages:
        text += format_message_preview(msg) + "\n"

        status = "✅" if msg['is_read'] else "📧"
        keyboard_buttons.append([
            types.InlineKeyboardButton(
                text=f"{status} {msg['subject'][:30]}...",
                callback_data=f"read_message:{msg['id']}"
            )
        ])

    keyboard_buttons.append([types.InlineKeyboardButton(text="🔙 Назад", callback_data=f"manage_email:{inbox_id}")])

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("read_message:"))
async def read_message(callback: types.CallbackQuery):
    """Read specific message"""
    message_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    message = await db.get_message(message_id)
    if not message:
        await callback.answer("❌ Письмо не найдено")
        return

    inbox = await db.get_inbox(message['inbox_id'])
    if not inbox or inbox['user_id'] != user_id:
        await callback.answer("❌ Доступ запрещен")
        return

    # Mark as read
    await db.mark_message_read(message_id)

    # Format message content
    subject = message.get('subject', 'Без темы')
    sender = message.get('sender', 'Неизвестный')
    body_html = message.get('body_html', '')
    body_text = message.get('body_text', '')

    text = f"📩 <b>{subject}</b>\n"
    text += f"👤 От: <code>{sender}</code>\n\n"

    # Use HTML body if available, otherwise text
    if body_html:
        # Clean up HTML for Telegram
        import re
        body = re.sub(r'<[^>]+>', '', body_html)
        body = body.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    else:
        body = body_text

    # Truncate if too long
    if len(body) > 3000:
        body = body[:3000] + "..."

    text += body

    await callback.message.edit_text(
        text,
        reply_markup=get_message_actions_keyboard(message_id, inbox['id']),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("extract_otp:"))
async def extract_otp(callback: types.CallbackQuery):
    """Extract OTP from message"""
    message_id = int(callback.data.split(":")[1])

    message = await db.get_message(message_id)
    if not message:
        await callback.answer("❌ Письмо не найдено")
        return

    async with FreeCustomAPIClient("") as client:  # Empty key since we don't need API for extraction
        body = message.get('body_html', '') or message.get('body_text', '')
        otp = client.extract_otp(body)

        if otp:
            await callback.message.answer(
                f"🤖 Найден код: <code>{otp}</code>",
                parse_mode=ParseMode.HTML
            )
            await callback.answer("OTP извлечен!")
        else:
            await callback.answer("🤖 Код не найден в письме")

@dp.callback_query(lambda c: c.data.startswith("delete_email:"))
async def delete_email_confirm(callback: types.CallbackQuery):
    """Confirm email deletion"""
    inbox_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    inbox = await db.get_inbox(inbox_id)
    if not inbox or inbox['user_id'] != user_id:
        await callback.answer("❌ Почтовый ящик не найден")
        return

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete:{inbox_id}")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"manage_email:{inbox_id}")]
    ])

    await callback.message.edit_text(
        f"🗑 Вы уверены, что хотите удалить почтовый ящик <b>{inbox['email']}</b>?\n\n"
        "Это действие нельзя отменить!",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("confirm_delete:"))
async def confirm_delete_email(callback: types.CallbackQuery):
    """Actually delete email"""
    inbox_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    inbox = await db.get_inbox(inbox_id)
    if not inbox or inbox['user_id'] != user_id:
        await callback.answer("❌ Почтовый ящик не найден")
        return

    user_data = await db.get_user(user_id)
    if user_data:
        try:
            async with FreeCustomAPIClient(user_data['api_key']) as client:
                await client.delete_email(inbox['email'])
        except FreeCustomAPIError:
            pass  # Continue with local deletion even if API fails

    await db.delete_inbox(inbox_id)

    await callback.message.edit_text(
        f"✅ Почтовый ящик <b>{inbox['email']}</b> успешно удален.",
        reply_markup=get_back_keyboard("list_emails")
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "api_settings")
async def api_settings(callback: types.CallbackQuery):
    """API settings menu"""
    user_data = await db.get_user(callback.from_user.id)

    text = "⚙️ Настройки API ключа\n\n"

    if user_data:
        # Mask API key for security
        masked_key = user_data['api_key'][:8] + "..." + user_data['api_key'][-4:]
        text += f"🔑 Текущий ключ: <code>{masked_key}</code>\n"
        text += f"📅 Настроен: {user_data['created_at'].strftime('%Y-%m-%d %H:%M')}"
    else:
        text += "❌ API ключ не настроен"

    await callback.message.edit_text(
        text,
        reply_markup=get_api_settings_keyboard()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "change_api_key")
async def change_api_key(callback: types.CallbackQuery, state: FSMContext):
    """Change API key"""
    await callback.message.edit_text(
        "🔄 Введите новый API ключ от сервиса FreeCustom.Email:",
        reply_markup=get_back_keyboard("api_settings")
    )
    await state.set_state(APIKeySetup.waiting_for_key)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "delete_api_key")
async def delete_api_key_confirm(callback: types.CallbackQuery):
    """Confirm API key deletion"""
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delete_api_key")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="api_settings")]
    ])

    await callback.message.edit_text(
        "❌ Вы уверены, что хотите удалить API ключ?\n\n"
        "Это приведет к удалению всех ваших почтовых ящиков и данных!\n"
        "Восстановление будет невозможно.",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "confirm_delete_api_key")
async def confirm_delete_api_key(callback: types.CallbackQuery):
    """Actually delete API key"""
    user_id = callback.from_user.id

    await db.delete_user(user_id)

    await callback.message.edit_text(
        "✅ API ключ и все связанные данные успешно удалены.\n\n"
        "Отправьте /start для настройки нового ключа.",
        reply_markup=None
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "help")
async def show_help(callback: types.CallbackQuery):
    """Show help information"""
    help_text = """
ℹ️ <b>FreeCustom Email Manager</b>

Этот бот позволяет управлять временными почтовыми ящиками через API сервиса FreeCustom.Email.

<b>Основные функции:</b>
📧 Создание временных email адресов
📨 Получение и чтение входящих писем
🤖 Автоматическое извлечение OTP кодов
🗑 Удаление почтовых ящиков
⚙️ Управление API ключами

<b>Как использовать:</b>
1. Получите API ключ на freecustom.email
2. Отправьте /start и настройте ключ
3. Создавайте почтовые ящики и используйте их для регистрации
4. Проверяйте входящие письма через бота

<b>Команды:</b>
/start - Запуск бота и настройка

<b>Поддержка:</b>
Если возникли проблемы, проверьте:
• Корректность API ключа
• Доступность сервиса freecustom.email
• Ваше интернет-соединение
"""

    await callback.message.edit_text(
        help_text,
        reply_markup=get_back_keyboard("back_to_main"),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

# Background tasks
async def check_new_emails():
    """Background task to check for new emails"""
    logger.info("Checking for new emails...")

    # Get all active inboxes
    # Note: In a real implementation, you might want to limit this to recently active users
    # For now, we'll check all active inboxes

    try:
        # This is a simplified version. In production, you'd want to track which users
        # have active sessions and only check those inboxes.

        # For now, we'll skip the automatic checking as it requires more complex logic
        # to track active user sessions. The manual refresh functionality is implemented instead.

        pass
    except Exception as e:
        logger.error(f"Error in check_new_emails: {e}")

async def check_expiring_emails():
    """Background task to check for expiring emails"""
    logger.info("Checking for expiring emails...")

    try:
        expiring_inboxes = await db.get_expiring_inboxes(Config.DEADLINE_WARNING_MINUTES)

        for inbox in expiring_inboxes:
            try:
                if bot:
                    await bot.send_message(
                        inbox['user_id'],
                        f"⚠️ Почта <b>{inbox['email']}</b> скоро истечет!\n\n"
                        f"⏰ Время жизни: {inbox['expires_at'].strftime('%Y-%m-%d %H:%M')}\n\n"
                        f"После истечения срока ящик будет автоматически удален.",
                        parse_mode=ParseMode.HTML
                    )
            except Exception as e:
                logger.error(f"Failed to send expiry warning to user {inbox['user_id']}: {e}")

    except Exception as e:
        logger.error(f"Error in check_expiring_emails: {e}")

# Startup and shutdown
async def on_startup():
    """Initialize bot"""
    logger.info("Bot starting up...")
    await init_database()

    # Start background tasks
    scheduler.add_job(
        check_expiring_emails,
        trigger=IntervalTrigger(seconds=Config.DEADLINE_CHECK_INTERVAL),
        id='check_expiring',
        name='Check expiring emails'
    )

    # Note: Automatic email checking is disabled for simplicity
    # In production, you'd implement user session tracking

    scheduler.start()
    logger.info("Bot started successfully")

async def on_shutdown():
    """Shutdown bot"""
    logger.info("Bot shutting down...")
    scheduler.shutdown()
    await close_database()
    if bot:
        await bot.session.close()
    logger.info("Bot shut down successfully")

# Main function
async def main():
    """Main bot function"""
    # Validate configuration
    try:
        Config.validate()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return

    # Initialize bot after config validation
    global bot
    bot = Bot(token=Config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Set up startup/shutdown handlers
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Start polling
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")

if __name__ == "__main__":
    asyncio.run(main())