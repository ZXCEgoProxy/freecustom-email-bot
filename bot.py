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
    waiting_for_profile_name = State()
    waiting_for_profile_key = State()
    waiting_for_profile_rename = State()

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
async def get_main_menu_keyboard_with_profile(user_id: int) -> types.InlineKeyboardMarkup:
    """Main menu keyboard with active profile info"""
    active_profile = await db.get_active_profile(user_id)
    inbox_count = 0

    if active_profile:
        inboxes = await db.get_profile_inboxes(active_profile['id'])
        inbox_count = len(inboxes)

    return get_main_menu_keyboard(active_profile, inbox_count)

def get_main_menu_keyboard(active_profile=None, inbox_count=0) -> types.InlineKeyboardMarkup:
    """Main menu keyboard"""
    keyboard = []

    # Show active profile info if available
    if active_profile:
        profile_text = f"🔑 {active_profile['profile_name']} (📧 {inbox_count})"
        keyboard.append([types.InlineKeyboardButton(text=profile_text, callback_data="switch_profile")])

    keyboard.extend([
        [types.InlineKeyboardButton(text="📬 Мои почты", callback_data="list_emails")],
        [types.InlineKeyboardButton(text="➕ Создать новую почту", callback_data="create_email")],
        [
            types.InlineKeyboardButton(text="🔄 Переключить API", callback_data="switch_profile"),
            types.InlineKeyboardButton(text="👤 Профили API", callback_data="api_profiles")
        ],
        [
            types.InlineKeyboardButton(text="🔑 Добавить API", callback_data="quick_add_api"),
            types.InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")
        ]
    ])
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

def get_api_profiles_keyboard() -> types.InlineKeyboardMarkup:
    """API profiles management keyboard"""
    keyboard = [
        [types.InlineKeyboardButton(text="⚙️ Управление профилями", callback_data="manage_profiles")],
        [types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_back_keyboard(callback_data: str = "back_to_main") -> types.InlineKeyboardMarkup:
    """Simple back button"""
    keyboard = [[types.InlineKeyboardButton(text="🔙 Назад", callback_data=callback_data)]]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)

async def get_inbox_owner_user_id(inbox_id: int) -> Optional[int]:
    """Get user_id of inbox owner through profile relationship"""
    inbox = await db.get_inbox(inbox_id)
    if not inbox:
        return None

    # Get profile to find user_id
    profile = await db.get_api_profile(inbox['profile_id'])
    return profile['user_id'] if profile else None

async def show_api_profiles(message_or_callback, user_id: int):
    """Show API profiles for user"""
    profiles = await db.get_user_api_profiles(user_id)
    active_profile = await db.get_active_profile(user_id)

    if not profiles:
        text = "❌ У вас нет API профилей.\n\nСоздайте первый профиль:"
        keyboard = [[types.InlineKeyboardButton(text="➕ Создать профиль", callback_data="add_api_profile")]]
    else:
        text = "👤 Ваши API профили:\n\n"
        keyboard = []

        for profile in profiles:
            status = "✅" if active_profile and active_profile['id'] == profile['id'] else "⚪"

            # Get inbox count for this profile
            inboxes = await db.get_profile_inboxes(profile['id'])
            inbox_count = len(inboxes)

            text += f"{status} <b>{profile['profile_name']}</b>\n"
            text += f"   🔑 {profile['api_key'][:10]}...{profile['api_key'][-4:]}\n"
            text += f"   📧 Почт: {inbox_count}\n"
            text += f"   📅 {profile['created_at'].strftime('%Y-%m-%d %H:%M')}\n\n"

            # Buttons for each profile: select, view emails, edit, delete
            keyboard.append([
                types.InlineKeyboardButton(
                    text=f"🎯 Выбрать",
                    callback_data=f"select_profile:{profile['id']}"
                ),
                types.InlineKeyboardButton(
                    text=f"📬 Почты ({inbox_count})",
                    callback_data=f"view_profile_emails:{profile['id']}"
                )
            ])
            keyboard.append([
                types.InlineKeyboardButton(
                    text=f"✏️ Изменить",
                    callback_data=f"edit_profile:{profile['id']}"
                ),
                types.InlineKeyboardButton(
                    text=f"🗑 Удалить",
                    callback_data=f"delete_profile:{profile['id']}"
                )
            ])

        keyboard.append([types.InlineKeyboardButton(text="➕ Добавить профиль", callback_data="add_api_profile")])
        keyboard.append([types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])

    reply_markup = types.InlineKeyboardMarkup(inline_keyboard=keyboard)

    if hasattr(message_or_callback, 'edit_text'):
        # It's a callback
        await message_or_callback.edit_text(text, reply_markup=reply_markup)
    else:
        # It's a message
        await message_or_callback.answer(text, reply_markup=reply_markup)

# Command handlers
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Handle /start command"""
    user_id = message.from_user.id

    # Create user if not exists
    await db.create_user(user_id)

    # Check if user has active API profile
    active_profile = await db.get_active_profile(user_id)

    if active_profile:
        # User has active profile, show main menu
        inboxes = await db.get_profile_inboxes(active_profile['id'])
        inbox_count = len(inboxes)

        await message.answer(
            f"👋 Добро пожаловать в FreeCustom Email Manager!\n\n"
            f"🔑 Активный профиль: <b>{active_profile['profile_name']}</b>\n"
            f"📧 Доступно почтовых ящиков: <b>{inbox_count}</b>\n\n"
            f"Выберите действие:",
            reply_markup=get_main_menu_keyboard(active_profile, inbox_count),
            parse_mode=ParseMode.HTML
        )
    else:
        # User needs to set up API profile
        profiles = await db.get_user_api_profiles(user_id)
        if profiles:
            # User has profiles but no active one - show profile selection
            await show_api_profiles(message, user_id)
        else:
            # No profiles - setup first profile
            await message.answer(
                "👋 Добро пожаловать в FreeCustom Email Manager!\n\n"
                "Для начала работы необходимо настроить API профиль FreeCustom.Email.\n\n"
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

    # Validate API key and create profile
    try:
        logger.info(f"Validating API key for user {user_id}: {api_key[:10]}...")
        async with FreeCustomAPIClient(api_key) as client:
            is_valid = await client.validate_api_key()
            logger.info(f"API key validation result: {is_valid}")

            if is_valid:
                # Check existing profiles to generate unique name
                existing_profiles = await db.get_user_api_profiles(user_id)
                profile_names = [p['profile_name'] for p in existing_profiles]

                # Generate unique profile name
                base_name = "Мой профиль"
                profile_name = base_name
                counter = 2
                while profile_name in profile_names:
                    profile_name = f"{base_name} {counter}"
                    counter += 1

                profile_id = await db.save_api_profile(user_id, profile_name, api_key)

                # Set as active if it's the first profile or if no active profile exists
                active_profile = await db.get_active_profile(user_id)
                if not active_profile:
                    await db.set_active_profile(user_id, profile_id)

                # Get updated profile info for menu
                updated_active_profile = await db.get_active_profile(user_id)
                updated_inboxes = await db.get_profile_inboxes(profile_id)
                updated_inbox_count = len(updated_inboxes)

                await message.answer(
                    "✅ API профиль успешно создан!\n\n"
                    "Теперь вы можете управлять временными почтовыми ящиками.",
                    reply_markup=get_main_menu_keyboard(updated_active_profile, updated_inbox_count)
                )
                await state.clear()
            else:
                await message.answer(
                    "❌ Неверный API ключ. Проверьте ключ и попробуйте еще раз:"
                )
    except FreeCustomAPIError as e:
        await message.answer(f"❌ Ошибка проверки ключа: {str(e)}\n\nПопробуйте еще раз:")

@dp.message(APIKeySetup.waiting_for_profile_name)
async def process_profile_name(message: types.Message, state: FSMContext):
    """Process profile name input"""
    profile_name = message.text.strip()
    if not profile_name:
        await message.answer("❌ Название не может быть пустым. Попробуйте еще раз:")
        return

    await state.update_data(profile_name=profile_name)
    await message.answer(
        f"✅ Название профиля: <b>{profile_name}</b>\n\n"
        "Теперь введите API ключ от FreeCustom.Email:",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(APIKeySetup.waiting_for_profile_key)

@dp.message(APIKeySetup.waiting_for_profile_key)
async def process_profile_key(message: types.Message, state: FSMContext):
    """Process API key for new profile"""
    user_id = message.from_user.id
    api_key = message.text.strip()
    state_data = await state.get_data()
    profile_name = state_data.get('profile_name')

    if not api_key:
        await message.answer("❌ API ключ не может быть пустым. Попробуйте еще раз:")
        return

    # Validate API key
    try:
        async with FreeCustomAPIClient(api_key) as client:
            is_valid = await client.validate_api_key()

            if is_valid:
                profile_id = await db.save_api_profile(user_id, profile_name, api_key)

                # Set as active if it's the first profile
                profiles = await db.get_user_api_profiles(user_id)
                if len(profiles) == 1:
                    await db.set_active_profile(user_id, profile_id)

                # Get updated profile info for menu
                updated_active_profile = await db.get_active_profile(user_id)
                updated_inboxes = await db.get_profile_inboxes(profile_id)
                updated_inbox_count = len(updated_inboxes)

                await message.answer(
                    f"✅ API профиль <b>{profile_name}</b> успешно создан!\n\n"
                    "Теперь вы можете переключаться между профилями.",
                    reply_markup=get_main_menu_keyboard(updated_active_profile, updated_inbox_count),
                    parse_mode=ParseMode.HTML
                )
                await state.clear()
            else:
                await message.answer("❌ Неверный API ключ. Проверьте ключ и попробуйте еще раз:")
    except Exception as e:
        await message.answer(f"❌ Ошибка валидации ключа: {str(e)}\n\nПопробуйте еще раз:")

@dp.message(APIKeySetup.waiting_for_profile_rename)
async def process_profile_rename(message: types.Message, state: FSMContext):
    """Process profile name change"""
    user_id = message.from_user.id
    new_name = message.text.strip()
    state_data = await state.get_data()
    profile_id = state_data.get('profile_id')

    if not new_name:
        await message.answer("❌ Название не может быть пустым. Попробуйте еще раз:")
        return

    if not profile_id:
        await message.answer("❌ Ошибка: профиль не найден. Попробуйте еще раз.")
        await state.clear()
        return

    # Verify profile belongs to user
    profile = await db.get_api_profile(profile_id)
    if not profile or profile['user_id'] != user_id:
        await message.answer("❌ Профиль не найден.")
        await state.clear()
        return

    # Update profile name
    try:
        await db.update_api_profile_name(profile_id, new_name)
        # Get updated profile info for menu
        updated_active_profile = await db.get_active_profile(user_id)
        updated_inboxes = []
        updated_inbox_count = 0
        if updated_active_profile:
            updated_inboxes = await db.get_profile_inboxes(updated_active_profile['id'])
            updated_inbox_count = len(updated_inboxes)

        await message.answer(
            f"✅ Название профиля изменено!\n\n"
            f"<b>{state_data.get('current_name', 'Старое название')}</b> → <b>{new_name}</b>",
            reply_markup=get_main_menu_keyboard(updated_active_profile, updated_inbox_count),
            parse_mode=ParseMode.HTML
        )
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Ошибка при изменении названия: {str(e)}")
        await state.clear()

# Callback query handlers
@dp.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    """Back to main menu"""
    user_id = callback.from_user.id
    menu_keyboard = await get_main_menu_keyboard_with_profile(user_id)

    await callback.message.edit_text(
        "Выберите действие:",
        reply_markup=menu_keyboard
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

    active_profile = await db.get_active_profile(user_id)
    if not active_profile:
        await callback.message.edit_text(
            "❌ Активный API профиль не найден. Выберите профиль в настройках.",
            reply_markup=get_back_keyboard("api_profiles")
        )
        await callback.answer()
        return

    inboxes = await db.get_profile_inboxes(active_profile['id'])

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
    inbox_owner_id = await get_inbox_owner_user_id(inbox_id)
    if not inbox or inbox_owner_id != user_id:
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

    active_profile = await db.get_active_profile(user_id)
    if not active_profile:
        await callback.message.edit_text(
            "❌ Активный API профиль не найден. Выберите профиль в настройках.",
            reply_markup=get_back_keyboard("api_profiles")
        )
        await callback.answer()
        return

    # Try to create email directly
    try:
        logger.info(f"Creating email for user {user_id} with profile {active_profile['profile_name']}: {active_profile['api_key'][:10]}...")
        async with FreeCustomAPIClient(active_profile['api_key']) as client:
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
            inbox_id = await db.save_inbox(active_profile['id'], email_data['email'], expires_at)

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
    inbox_owner_id = await get_inbox_owner_user_id(inbox_id)
    if not inbox or inbox_owner_id != user_id:
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
    inbox_owner_id = await get_inbox_owner_user_id(inbox_id)
    if not inbox or inbox_owner_id != user_id:
        await callback.answer("❌ Почтовый ящик не найден")
        return

    active_profile = await db.get_active_profile(user_id)
    if not active_profile:
        await callback.answer("❌ Активный API профиль не найден")
        return

    try:
        async with FreeCustomAPIClient(active_profile['api_key']) as client:
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
    inbox_owner_id = await get_inbox_owner_user_id(inbox_id)
    if not inbox or inbox_owner_id != user_id:
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
    inbox_owner_id = await get_inbox_owner_user_id(message['inbox_id'])
    if not inbox or inbox_owner_id != user_id:
        await callback.answer("❌ Доступ запрещен")
        return

    # Mark as read
    await db.mark_message_read(message_id)

    # Check if we have full message content, if not - fetch from API
    body_html = message.get('body_html', '')
    body_text = message.get('body_text', '')

    if not body_html and not body_text:
        # Fetch full message content from API
        active_profile = await db.get_active_profile(user_id)
        if active_profile:
            try:
                async with FreeCustomAPIClient(active_profile['api_key']) as client:
                    full_message = await client.get_message(inbox['email'], str(message['message_id']))
                    body_html = full_message.get('html', '')
                    body_text = full_message.get('text', '')

                    # Update message in database with full content
                    await db.save_message(message['inbox_id'], {
                        'id': message['message_id'],
                        'subject': message.get('subject'),
                        'from': message.get('sender'),
                        'date': message.get('received_at'),
                        'html': body_html,
                        'text': body_text
                    })
            except Exception as e:
                logger.error(f"Failed to fetch full message content: {e}")

    # Format message content
    subject = message.get('subject', 'Без темы')
    sender = message.get('sender', 'Неизвестный')

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
    inbox_owner_id = await get_inbox_owner_user_id(inbox_id)
    if not inbox or inbox_owner_id != user_id:
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
    inbox_owner_id = await get_inbox_owner_user_id(inbox_id)
    if not inbox or inbox_owner_id != user_id:
        await callback.answer("❌ Почтовый ящик не найден")
        return

    # Get active profile for API operations
    active_profile = await db.get_active_profile(user_id)
    if active_profile:
        try:
            async with FreeCustomAPIClient(active_profile['api_key']) as client:
                await client.delete_email(inbox['email'])
        except FreeCustomAPIError:
            pass  # Continue with local deletion even if API fails

    await db.delete_inbox(inbox_id)

    await callback.message.edit_text(
        f"✅ Почтовый ящик <b>{inbox['email']}</b> успешно удален.",
        reply_markup=get_back_keyboard("list_emails")
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "api_profiles")
async def api_profiles_menu(callback: types.CallbackQuery):
    """API profiles menu"""
    await show_api_profiles(callback, callback.from_user.id)
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
🔑 Управление API профилями (несколько аккаунтов)
📧 Создание временных email адресов
📨 Получение и чтение входящих писем
🤖 Автоматическое извлечение OTP кодов
🗑 Удаление почтовых ящиков
⚙️ Управление API ключами

<b>Как использовать:</b>
1. Получите API ключ на freecustom.email
2. Добавьте API профиль через "🔑 Добавить API"
3. Переключайтесь между профилями через "🔄 Переключить API"
4. Создавайте почтовые ящики (каждый профиль имеет свои почты)
5. Управляйте почтами в "📬 Мои почты" (активного профиля)
6. Просматривайте все профили и их почты в "👤 Профили API"

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

@dp.callback_query(lambda c: c.data.startswith("select_profile:"))
async def select_profile(callback: types.CallbackQuery):
    """Select active API profile"""
    profile_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    # Verify profile belongs to user
    profile = await db.get_api_profile(profile_id)
    if not profile or profile['user_id'] != user_id:
        await callback.answer("❌ Профиль не найден")
        return

    await db.set_active_profile(user_id, profile_id)

    # Get inbox count for this profile
    inboxes = await db.get_profile_inboxes(profile_id)
    inbox_count = len(inboxes)

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📬 Управлять почтами", callback_data="list_emails")],
        [types.InlineKeyboardButton(text="➕ Создать новую почту", callback_data="create_email")],
        [types.InlineKeyboardButton(text="🔙 К профилям", callback_data="api_profiles")]
    ])

    await callback.message.edit_text(
        f"✅ Активный профиль изменен на: <b>{profile['profile_name']}</b>\n\n"
        f"📧 Доступно почтовых ящиков: {inbox_count}\n\n"
        f"Теперь все операции будут использовать этот API ключ.",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("check_recent_messages:"))
async def check_recent_messages(callback: types.CallbackQuery):
    """Check and display recent messages for an inbox"""
    inbox_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    inbox = await db.get_inbox(inbox_id)
    inbox_owner_id = await get_inbox_owner_user_id(inbox_id)
    if not inbox or inbox_owner_id != user_id:
        await callback.answer("❌ Почтовый ящик не найден")
        return

    # Get inbox owner profile for API operations
    profile = await db.get_api_profile(inbox['profile_id'])
    if not profile:
        await callback.answer("❌ Профиль не найден")
        return

    try:
        # Fetch recent messages from API
        async with FreeCustomAPIClient(profile['api_key']) as client:
            messages_data = await client.get_messages(inbox['email'], limit=5, offset=0)

            # Save new messages to database
            for msg_data in messages_data:
                await db.save_message(inbox_id, msg_data)

        # Get messages from database (including newly fetched)
        messages = await db.get_inbox_messages(inbox_id)
        recent_messages = messages[:5]  # Show last 5 messages

        if not recent_messages:
            await callback.message.edit_text(
                f"📭 В почтовом ящике <b>{inbox['email']}</b> пока нет писем.\n\n"
                f"Попробуйте позже или используйте этот адрес для регистрации.",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="🔄 Проверить снова", callback_data=f"check_recent_messages:{inbox_id}")],
                    [types.InlineKeyboardButton(text="🔙 Назад", callback_data=f"view_profile_emails:{inbox['profile_id']}")]
                ]),
                parse_mode=ParseMode.HTML
            )
            await callback.answer()
            return

        text = f"📨 Последние письма в <b>{inbox['email']}</b>:\n\n"

        keyboard = []
        for msg in recent_messages:
            status = "✅" if msg['is_read'] else "📧"
            subject = msg.get('subject', 'Без темы')[:30]
            sender = msg.get('sender', 'Неизвестный')[:20]
            received_at = msg.get('received_at')

            text += f"{status} <b>{subject}</b>\n"
            text += f"   👤 {sender}\n"
            if received_at:
                if isinstance(received_at, str):
                    text += f"   🕒 {received_at[:19]}\n"
                else:
                    text += f"   🕒 {received_at.strftime('%Y-%m-%d %H:%M')}\n"
            text += "\n"

            # Button to read full message
            keyboard.append([
                types.InlineKeyboardButton(
                    text=f"📖 {subject[:20]}...",
                    callback_data=f"read_message:{msg['id']}"
                )
            ])

        keyboard.append([
            types.InlineKeyboardButton(text="🔄 Обновить", callback_data=f"check_recent_messages:{inbox_id}"),
            types.InlineKeyboardButton(text="🔙 Назад", callback_data=f"view_profile_emails:{inbox['profile_id']}")
        ])

        await callback.message.edit_text(
            text,
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode=ParseMode.HTML
        )

    except FreeCustomAPIError as e:
        await callback.message.edit_text(
            f"❌ Ошибка при проверке почты <b>{inbox['email']}</b>:\n\n{str(e)}\n\n"
            f"Возможно, API ключ истек или сервис недоступен.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔄 Попробовать снова", callback_data=f"check_recent_messages:{inbox_id}")],
                [types.InlineKeyboardButton(text="🔙 Назад", callback_data=f"view_profile_emails:{inbox['profile_id']}")]
            ]),
            parse_mode=ParseMode.HTML
        )

    await callback.answer()

@dp.callback_query(lambda c: c.data == "manage_profiles")
async def manage_profiles(callback: types.CallbackQuery):
    """Show profiles management menu"""
    await show_api_profiles(callback, callback.from_user.id)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "switch_profile")
async def switch_profile_menu(callback: types.CallbackQuery):
    """Show profile switching menu"""
    user_id = callback.from_user.id
    profiles = await db.get_user_api_profiles(user_id)
    active_profile = await db.get_active_profile(user_id)

    if not profiles:
        await callback.message.edit_text(
            "❌ У вас нет API профилей.\n\n"
            "Сначала добавьте хотя бы один профиль.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔑 Добавить API", callback_data="quick_add_api")],
                [types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
            ])
        )
        await callback.answer()
        return

    text = "🔄 <b>Переключение API профиля</b>\n\n"
    text += "Выберите профиль для работы:\n\n"

    keyboard = []
    for profile in profiles:
        status = "✅ АКТИВНЫЙ" if active_profile and active_profile['id'] == profile['id'] else "⚪"

        # Get inbox count for this profile
        inboxes = await db.get_profile_inboxes(profile['id'])
        inbox_count = len(inboxes)

        text += f"{status} <b>{profile['profile_name']}</b>\n"
        text += f"   📧 Почт: {inbox_count} | 🔑 ...{profile['api_key'][-4:]}\n\n"

        # Button to switch to this profile
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"🎯 Выбрать {profile['profile_name']}",
                callback_data=f"select_profile:{profile['id']}"
            )
        ])

    keyboard.append([types.InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "quick_add_api")
async def quick_add_api_start(callback: types.CallbackQuery, state: FSMContext):
    """Quick add API key without profile name"""
    await callback.message.edit_text(
        "🔑 <b>Быстрое добавление API ключа</b>\n\n"
        "Отправьте API ключ от FreeCustom.Email.\n"
        "Профиль будет создан автоматически.",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(APIKeySetup.waiting_for_key)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("view_profile_emails:"))
async def view_profile_emails(callback: types.CallbackQuery):
    """View all emails for a specific profile"""
    profile_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    # Verify profile belongs to user
    profile = await db.get_api_profile(profile_id)
    if not profile or profile['user_id'] != user_id:
        await callback.answer("❌ Профиль не найден")
        return

    inboxes = await db.get_profile_inboxes(profile_id)

    if not inboxes:
        await callback.message.edit_text(
            f"📭 В профиле <b>{profile['profile_name']}</b> пока нет почтовых ящиков.\n\n"
            f"Выберите этот профиль и создайте первую почту.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🎯 Выбрать профиль", callback_data=f"select_profile:{profile_id}")],
                [types.InlineKeyboardButton(text="🔙 Назад", callback_data="api_profiles")]
            ]),
            parse_mode=ParseMode.HTML
        )
        await callback.answer()
        return

    text = f"📬 Почтовые ящики профиля <b>{profile['profile_name']}</b>:\n\n"

    keyboard = []
    for inbox in inboxes:
        # Get message count for this inbox
        messages = await db.get_inbox_messages(inbox['id'])
        message_count = len(messages)

        text += f"📧 <b>{inbox['email']}</b>\n"
        text += f"   📨 Писем: {message_count}\n"
        text += f"   📅 Создан: {inbox['created_at'].strftime('%Y-%m-%d %H:%M')}\n\n"

        # Button to check recent messages
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"📨 Проверить ({message_count})",
                callback_data=f"check_recent_messages:{inbox['id']}"
            )
        ])

    keyboard.append([types.InlineKeyboardButton(text="🔙 К профилям", callback_data="api_profiles")])

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("edit_profile:"))
async def edit_profile_start(callback: types.CallbackQuery, state: FSMContext):
    """Start editing profile name"""
    profile_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    # Verify profile belongs to user
    profile = await db.get_api_profile(profile_id)
    if not profile or profile['user_id'] != user_id:
        await callback.answer("❌ Профиль не найден")
        return

    # Store profile_id in state
    await state.update_data(profile_id=profile_id, current_name=profile['profile_name'])

    await callback.message.edit_text(
        f"✏️ Изменение названия профиля\n\n"
        f"Текущее название: <b>{profile['profile_name']}</b>\n\n"
        f"Введите новое название профиля:",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(APIKeySetup.waiting_for_profile_rename)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("delete_profile:"))
async def delete_profile_confirm(callback: types.CallbackQuery):
    """Confirm profile deletion"""
    profile_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    # Verify profile belongs to user
    profile = await db.get_api_profile(profile_id)
    if not profile or profile['user_id'] != user_id:
        await callback.answer("❌ Профиль не найден")
        return

    # Check if this is the only profile
    all_profiles = await db.get_user_api_profiles(user_id)
    if len(all_profiles) <= 1:
        await callback.message.edit_text(
            "❌ Нельзя удалить единственный API профиль!\n\n"
            "Добавьте другой профиль перед удалением этого.",
            reply_markup=get_back_keyboard("api_profiles")
        )
        await callback.answer()
        return

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Да, удалить профиль", callback_data=f"confirm_delete_profile:{profile_id}")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="api_profiles")]
    ])

    # Count inboxes for this profile
    inboxes = await db.get_profile_inboxes(profile_id)
    inbox_count = len(inboxes)

    warning_text = f"🗑 Удаление профиля <b>{profile['profile_name']}</b>\n\n"
    if inbox_count > 0:
        warning_text += f"⚠️ Внимание: будут удалены {inbox_count} почтовых ящиков!\n\n"
    warning_text += "Это действие нельзя отменить. Продолжить?"

    await callback.message.edit_text(
        warning_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("confirm_delete_profile:"))
async def confirm_delete_profile(callback: types.CallbackQuery):
    """Actually delete the profile"""
    profile_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    # Verify profile belongs to user
    profile = await db.get_api_profile(profile_id)
    if not profile or profile['user_id'] != user_id:
        await callback.answer("❌ Профиль не найден")
        return

    # Check if this profile is currently active
    active_profile = await db.get_active_profile(user_id)
    was_active = active_profile and active_profile['id'] == profile_id

    # Delete the profile (CASCADE will handle related data)
    await db.delete_api_profile(profile_id)

    # If this was the active profile, set another profile as active
    if was_active:
        remaining_profiles = await db.get_user_api_profiles(user_id)
        if remaining_profiles:
            await db.set_active_profile(user_id, remaining_profiles[0]['id'])

    await callback.message.edit_text(
        f"✅ Профиль <b>{profile['profile_name']}</b> успешно удален.",
        reply_markup=get_back_keyboard("api_profiles"),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "add_api_profile")
async def add_api_profile_start(callback: types.CallbackQuery, state: FSMContext):
    """Start adding new API profile"""
    await callback.message.edit_text(
        "➕ Добавление нового API профиля\n\n"
        "Введите название профиля (например, 'Рабочий', 'Личный'):"
    )
    await state.set_state(APIKeySetup.waiting_for_profile_name)
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
                # Get user_id from profile
                user_id = await get_inbox_owner_user_id(inbox['id'])
                if user_id and bot:
                    await bot.send_message(
                        user_id,
                        f"⚠️ Почта <b>{inbox['email']}</b> скоро истечет!\n\n"
                        f"⏰ Время жизни: {inbox['expires_at'].strftime('%Y-%m-%d %H:%M')}\n\n"
                        f"После истечения срока ящик будет автоматически удален.",
                        parse_mode=ParseMode.HTML
                    )
            except Exception as e:
                logger.error(f"Failed to send expiry warning for inbox {inbox['id']}: {e}")

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