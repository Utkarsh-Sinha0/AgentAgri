"""
AgriMesh V4.0 — Telegram Bot (Primary Farmer Channel)
Handles: /start, /register, /field, /crop, photo uploads, text queries.
Routes all advisory queries through the Agent Orchestrator.
"""
from __future__ import annotations

# ─── Bot Setup ────────────────────────────────────────────────────────
# We use python-telegram-bot v21+ (async)
import asyncio
import re
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import settings
from app.database import async_session_factory
from app.models import (
    Advisory,
    AlertCluster,
    CropCalendarTask,
    CropCycle,
    Farmer,
    Field,
    Observation,
)
from app.services.agent import AgentContext, get_agent
from app.services.demo_seed import seed_demo_memory_palace
from app.utils.security import hash_password

# ─── Session storage (in-memory for demo; Redis in production) ────────

user_state: dict[str, dict] = {}  # telegram_id -> state dict

def get_user_state(user_id: str) -> dict:
    if user_id not in user_state:
        user_state[user_id] = {"state": "start", "data": {}}
    return user_state[user_id]


# ─── Handlers ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    user_id = str(user.id)
    state = get_user_state(user_id)

    welcome = (
        "🌾 *AgriMesh* — आपका AI कृषि सलाहकार\n"
        "Your AI Agricultural Advisor\n\n"
        "मैं आपकी फसल की बीमारियों की पहचान, मौसम की सलाह, मंडी भाव, "
        "सरकारी योजनाओं और खेती के खर्च का हिसाब रखने में मदद करता हूं।\n\n"
        "I help with crop disease identification, weather advice, market prices, "
        "government schemes, and farm finance tracking.\n\n"
        "*शुरू करें / आरंभ करें:*\n"
        "1. /demo — तुरंत डेमो खेत और मेमोरी तैयार करें\n"
        "2. /register — अपना पंजीकरण करें\n"
        "3. /field — अपना खेत पंजीकृत करें\n"
        "4. /crop — अपनी फसल की जानकारी दें\n"
        "5. फोटो भेजें या समस्या लिखें 📸\n\n"
        "📞 किसान कॉल सेंटर: 1800-180-1551"
    )

    keyboard = [
        [InlineKeyboardButton("⚡ डेमो चालू करें / Start Demo", callback_data="cmd_demo")],
        [InlineKeyboardButton("📝 रजिस्टर / Register", callback_data="cmd_register")],
        [InlineKeyboardButton("🌱 फसल / Crop", callback_data="cmd_crop")],
        [InlineKeyboardButton("📸 फोटो भेजें / Send Photo", callback_data="cmd_photo")],
        [InlineKeyboardButton("ℹ️ सहायता / Help", callback_data="cmd_help")],
    ]

    await update.message.reply_text(
        welcome,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    state["state"] = "start"


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /register command."""
    user = update.effective_user
    user_id = str(user.id)
    state = get_user_state(user_id)
    state["state"] = "registering_name"

    await update.message.reply_text(
        "📝 *किसान पंजीकरण / Farmer Registration*\n\n"
        "कृपया अपना पूरा नाम लिखें:\n"
        "Please enter your full name:",
        parse_mode="Markdown",
    )


async def field_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /field command."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)

    # Check if registered
    async with async_session_factory() as db:
        result = await db.execute(
            select(Farmer).where(Farmer.phone == user_id)
        )
        farmer = result.scalar_one_or_none()
        if not farmer:
            await update.message.reply_text(
                "⚠️ पहले /register करें। Please /register first."
            )
            return

    state["state"] = "registering_field_name"
    await update.message.reply_text(
        "🌿 *खेत पंजीकरण / Field Registration*\n\n"
        "खेत का नाम लिखें (जैसे: 'पूरब वाला खेत', 'निचला खेत'):\n"
        "Enter field name (e.g., 'East Field', 'Lower Plot'):",
        parse_mode="Markdown",
    )


async def crop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /crop command."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    state["state"] = "registering_crop_name"

    await update.message.reply_text(
        "🌱 *फसल जानकारी / Crop Information*\n\n"
        "फसल का नाम लिखें (जैसे: धान, गेहूं, मक्का, अरहर):\n"
        "Enter crop name (e.g., rice, wheat, maize, arhar):",
        parse_mode="Markdown",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all text messages based on user state."""
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    state = get_user_state(user_id)
    current_state = state.get("state", "start")

    if current_state == "registering_name":
        await _handle_name_registration(update, user_id, text, state)
    elif current_state == "registering_phone":
        await _handle_phone_registration(update, user_id, text, state)
    elif current_state == "registering_district":
        await _handle_district_registration(update, user_id, text, state)
    elif current_state == "registering_field_name":
        await _handle_field_name(update, user_id, text, state)
    elif current_state == "registering_field_area":
        await _handle_field_area(update, user_id, text, state)
    elif current_state == "registering_field_soil":
        await _handle_field_soil(update, user_id, text, state)
    elif current_state == "registering_crop_name":
        await _handle_crop_name(update, user_id, text, state)
    elif current_state == "registering_crop_stage":
        await _handle_crop_stage(update, user_id, text, state)
    elif current_state == "ready":
        # §6.7: Try structured data capture first (no LLM cost)
        captured = await try_structured_capture(update, user_id, text)
        if not captured:
            await _handle_farmer_query(update, user_id, text)
    else:
        # Default: treat as query if user seems registered, else guide
        await _handle_farmer_query(update, user_id, text)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo uploads (crop photos for disease diagnosis)."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)

    if state.get("state") != "ready":
        await update.message.reply_text("⚠️ पहले /register, /field, और /crop करें। कृपया पहले पंजीकरण पूरा करें।")
        return

    await update.message.send_chat_action(ChatAction.TYPING)

    # Download photo
    photo_file = await update.message.photo[-1].get_file()
    photo_dir = Path(settings.data_dir) / "photos"
    photo_dir.mkdir(parents=True, exist_ok=True)
    photo_path = photo_dir / f"{user_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jpg"
    await photo_file.download_to_drive(str(photo_path))

    caption = update.message.caption or ""
    await _process_farmer_query(update, user_id, caption, str(photo_path))


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)

    if data == "cmd_demo":
        await _activate_demo_memory(query.message, user_id, state)
    elif data == "cmd_register":
        state["state"] = "registering_name"
        await query.message.reply_text(
            "📝 *किसान पंजीकरण / Farmer Registration*\n\n"
            "कृपया अपना पूरा नाम लिखें:\n"
            "Please enter your full name:",
            parse_mode="Markdown",
        )
    elif data == "cmd_crop":
        state["state"] = "registering_crop_name"
        await query.message.reply_text(
            "🌱 *फसल जानकारी / Crop Information*\n\n"
            "फसल का नाम लिखें (जैसे: धान, गेहूं, मक्का, अरहर):\n"
            "Enter crop name (e.g., rice, wheat, maize, arhar):",
            parse_mode="Markdown",
        )
    elif data == "cmd_help":
        await query.message.reply_text(
            "ℹ️ सहायता / Help\n\n"
            "/register — किसान पंजीकरण\n"
            "/field — खेत पंजीकरण\n"
            "/crop — फसल जानकारी\n"
            "/prices — मंडी भाव\n"
            "/finance — खेत का हिसाब\n\n"
            "फसल की समस्या लिखें या फोटो भेजें।"
        )
    elif data == "cmd_photo":
        await query.message.reply_text(
            "📸 कृपया अपनी फसल की फोटो भेजें।\n"
            "लक्षण (लिखित में) भी बताएं।\n\n"
            "Please send a photo of your crop.\n"
            "Also describe the symptoms in text."
        )
    elif data == "cmd_finance":
        await query.message.reply_text(
            "💰 खर्च ऐसे जोड़ें:\n"
            "/expense fertilizer 500 DAP\n\n"
            "या सीधे लिखें: 2 bag urea @ 320"
        )
    elif data == "show_evidence":
        cards = state.get("last_evidence", [])
        if not cards:
            await query.message.reply_text("📋 अभी कोई साक्ष्य कार्ड उपलब्ध नहीं।")
        else:
            lines = ["📋 *Evidence Cards*"]
            for card in cards[:5]:
                lines.append(f"\n*{card.get('label', 'Evidence')}*\n{card.get('content', '')}")
            await query.message.reply_text("\n".join(lines), parse_mode="Markdown")
    elif data == "show_verifier":
        verifier = state.get("last_verifier", {})
        status = "✅ Passed" if verifier.get("passes_all") else "⚠️ Safe fallback used"
        await query.message.reply_text(
            f"{status}\n\n{verifier.get('details', 'No verifier details available.')}"
        )
    elif data.startswith("soil_"):
        soil_map = {
            "soil_loam": "loam",
            "soil_clay": "clay",
            "soil_sandy": "sandy",
            "soil_black": "black_cotton",
        }
        await _persist_field_from_callback(query, user_id, soil_map.get(data, "loam"), state)
    elif data.startswith("stage_"):
        stage = data.replace("stage_", "", 1)
        await _persist_crop_stage_from_callback(query, user_id, stage, state)


# ─── Registration Handlers ────────────────────────────────────────────

async def _handle_name_registration(update, user_id: str, name: str, state: dict):
    state["data"]["name"] = name
    state["state"] = "registering_phone"
    await update.message.reply_text(
        f"नमस्ते {name}! 👋\n\nअपना फोन नंबर लिखें (10 अंक):\nEnter your 10-digit phone number:"
    )


async def _handle_phone_registration(update, user_id: str, phone: str, state: dict):
    phone = phone.replace(" ", "").replace("-", "").replace("+91", "")
    state["data"]["phone"] = phone
    state["state"] = "registering_district"
    await update.message.reply_text("अपना जिला लिखें / Enter your district (e.g., Munger):")


async def _handle_district_registration(update, user_id: str, district: str, state: dict):
    state["data"]["district"] = district
    state["data"]["preferred_language"] = "hi"

    # Persist farmer
    async with async_session_factory() as db:
        import uuid
        existing = await db.execute(select(Farmer).where(Farmer.phone == user_id))
        farmer = existing.scalar_one_or_none()
        if farmer:
            farmer.name = state["data"]["name"]
            farmer.preferred_language = "hi"
            farmer.district = district
            farmer.is_active = True
        else:
            farmer = Farmer(
                id=str(uuid.uuid4()),
                phone=user_id,
                hashed_password=hash_password(f"telegram:{user_id}"),
                name=state["data"]["name"],
                preferred_language="hi",
                district=district,
                tehsil="",
                village="",
            )
            db.add(farmer)
        try:
            await db.commit()
            state["farmer_id"] = farmer.id
            state["phone"] = user_id
        except Exception as exc:
            await db.rollback()
            logger.error(f"Farmer registration failed: {exc}")
            await update.message.reply_text("❌ पंजीकरण में त्रुटि। कृपया /register पुनः करें।")
            state["state"] = "start"
            return

    state["state"] = "start"
    await update.message.reply_text(
        "✅ *पंजीकरण सफल!*\n\n"
        "अब /field से अपना खेत पंजीकृत करें।\n"
        "Now register your field with /field",
        parse_mode="Markdown",
    )


async def _handle_field_name(update, user_id: str, name: str, state: dict):
    state["data"]["field_name"] = name
    state["state"] = "registering_field_area"
    await update.message.reply_text("खेत का क्षेत्रफल (एकड़ में) / Field area in acres:")


async def _handle_field_area(update, user_id: str, area_str: str, state: dict):
    try:
        area = float(area_str)
    except ValueError:
        await update.message.reply_text("⚠️ कृपया संख्या लिखें। Please enter a number (e.g., 2.5).")
        return

    state["data"]["field_area"] = area
    state["state"] = "registering_field_soil"

    keyboard = [
        [InlineKeyboardButton("दोमट / Loam", callback_data="soil_loam")],
        [InlineKeyboardButton("चिकनी / Clay", callback_data="soil_clay")],
        [InlineKeyboardButton("बलुई / Sandy", callback_data="soil_sandy")],
        [InlineKeyboardButton("काली / Black Cotton", callback_data="soil_black")],
    ]
    await update.message.reply_text(
        "मिट्टी का प्रकार / Soil type:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _handle_field_soil(update, user_id: str, soil: str, state: dict):
    state["data"]["soil_type"] = soil

    async with async_session_factory() as db:
        # Find farmer
        phone = state.get("phone", user_id)
        result = await db.execute(select(Farmer).where(Farmer.phone == phone))
        farmer = result.scalar_one_or_none()

        if not farmer:
            await update.message.reply_text("⚠️ पहले /register करें।")
            return

        import uuid
        field = Field(
            id=str(uuid.uuid4()),
            farmer_id=farmer.id,
            name=state["data"].get("field_name", "Default Field"),
            area_acres=state["data"].get("field_area", 1.0),
            soil_type=soil,
            irrigation_type="rainfed",
        )
        db.add(field)
        try:
            await db.commit()
            state["field_id"] = field.id
        except Exception as exc:
            await db.rollback()
            logger.error(f"Field registration failed: {exc}")
            return

    state["state"] = "start"
    await update.message.reply_text(
        "✅ *खेत पंजीकृत!*\n\nअब /crop से अपनी फसल दर्ज करें।",
        parse_mode="Markdown",
    )


async def _handle_crop_name(update, user_id: str, crop_name: str, state: dict):
    state["data"]["crop_name"] = crop_name
    state["state"] = "registering_crop_stage"

    await update.message.reply_text(
        "फसल की अवस्था / Crop stage:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🌱 अंकुरण / Seedling", callback_data="stage_seedling")],
            [InlineKeyboardButton("🌿 वानस्पतिक / Vegetative", callback_data="stage_vegetative")],
            [InlineKeyboardButton("🌸 फूल / Flowering", callback_data="stage_flowering")],
            [InlineKeyboardButton("🌾 फल/दाना / Fruiting", callback_data="stage_fruiting")],
            [InlineKeyboardButton("🔪 कटाई / Harvest", callback_data="stage_harvest")],
        ]),
        )


async def _persist_field_from_callback(query, user_id: str, soil: str, state: dict):
    state["data"]["soil_type"] = soil

    async with async_session_factory() as db:
        phone = state.get("phone", user_id)
        result = await db.execute(select(Farmer).where(Farmer.phone == phone))
        farmer = result.scalar_one_or_none()

        if not farmer:
            await query.message.reply_text("⚠️ पहले /register करें।")
            return

        import uuid
        field = Field(
            id=str(uuid.uuid4()),
            farmer_id=farmer.id,
            name=state["data"].get("field_name", "Default Field"),
            area_acres=state["data"].get("field_area", 1.0),
            soil_type=soil,
            irrigation_type="rainfed",
        )
        db.add(field)
        try:
            await db.commit()
            state["field_id"] = field.id
        except Exception as exc:
            await db.rollback()
            logger.error(f"Field registration failed: {exc}")
            await query.message.reply_text("❌ खेत पंजीकरण में त्रुटि। कृपया फिर कोशिश करें।")
            return

    state["state"] = "start"
    await query.message.reply_text(
        "✅ *खेत पंजीकृत!*\n\nअब /crop से अपनी फसल दर्ज करें।",
        parse_mode="Markdown",
    )


async def _persist_crop_stage_from_callback(query, user_id: str, stage: str, state: dict):
    state["data"]["crop_stage"] = stage

    async with async_session_factory() as db:
        phone = state.get("phone", user_id)
        result = await db.execute(select(Farmer).where(Farmer.phone == phone))
        farmer = result.scalar_one_or_none()
        if not farmer:
            await query.message.reply_text("⚠️ पहले /register करें।")
            return

        field_result = await db.execute(
            select(Field).where(Field.farmer_id == farmer.id).limit(1)
        )
        field = field_result.scalar_one_or_none()
        if not field:
            await query.message.reply_text("⚠️ पहले /field से अपना खेत पंजीकृत करें।")
            return

        import uuid
        cycle = CropCycle(
            id=str(uuid.uuid4()),
            field_id=field.id,
            crop_name=state["data"].get("crop_name", "rice"),
            sowing_date=datetime.utcnow(),
            current_stage=stage,
            is_active=True,
        )
        db.add(cycle)
        try:
            await db.commit()
            state["crop_cycle_id"] = cycle.id
            state["crop_name"] = cycle.crop_name
            state["crop_stage"] = cycle.current_stage
            state["field_id"] = field.id
            state["field_id"] = field.id
        except Exception as exc:
            await db.rollback()
            logger.error(f"Crop cycle creation failed: {exc}")
            await query.message.reply_text("❌ फसल दर्ज करने में त्रुटि। कृपया फिर कोशिश करें।")
            return

    state["state"] = "ready"
    await query.message.reply_text(
        "✅ *सब तैयार! / All Set!*\n\n"
        "अब आप अपनी फसल की फोटो भेज सकते हैं या समस्या लिख सकते हैं।",
        parse_mode="Markdown",
    )


async def _handle_crop_stage(update, user_id: str, stage: str, state: dict):
    state["data"]["crop_stage"] = stage

    async with async_session_factory() as db:
        phone = state.get("phone", user_id)
        result = await db.execute(select(Farmer).where(Farmer.phone == phone))
        farmer = result.scalar_one_or_none()
        if not farmer:
            await update.message.reply_text("⚠️ पहले /register करें।")
            return

        # Find field
        field_result = await db.execute(
            select(Field).where(Field.farmer_id == farmer.id).limit(1)
        )
        field = field_result.scalar_one_or_none()
        if not field:
            await update.message.reply_text("⚠️ पहले /field से अपना खेत पंजीकृत करें।")
            return

        import uuid
        cycle = CropCycle(
            id=str(uuid.uuid4()),
            field_id=field.id,
            crop_name=state["data"]["crop_name"],
            sowing_date=datetime.utcnow(),
            current_stage=stage,
            is_active=True,
        )
        db.add(cycle)
        try:
            await db.commit()
            state["crop_cycle_id"] = cycle.id
            state["crop_name"] = cycle.crop_name
            state["crop_stage"] = cycle.current_stage
        except Exception as exc:
            await db.rollback()
            logger.error(f"Crop cycle creation failed: {exc}")
            return

    state["state"] = "ready"
    await update.message.reply_text(
        "✅ *सब तैयार! / All Set!*\n\n"
        "अब आप अपनी फसल की फोटो भेज सकते हैं या समस्या लिख सकते हैं।\n"
        "मैं आपकी मदद करूंगा! 🌾\n\n"
        "📸 फोटो भेजें 📝 या लिखें...",
        parse_mode="Markdown",
    )


# ─── Query Handler ────────────────────────────────────────────────────

async def _handle_farmer_query(update, user_id: str, text: str):
    await _process_farmer_query(update, user_id, text, image_path=None)


async def _process_farmer_query(
    update,
    user_id: str,
    text: str,
    image_path: str | None = None,
):
    """Route a farmer query through the Agent Orchestrator."""
    state = get_user_state(user_id)
    phone = state.get("phone", user_id)

    await update.message.send_chat_action(ChatAction.TYPING)

    # Get farmer context from DB
    async with async_session_factory() as db:
        result = await db.execute(select(Farmer).where(Farmer.phone == phone))
        farmer = result.scalar_one_or_none()

        if not farmer:
            await update.message.reply_text("⚠️ पहले /register करें। Please /register first.")
            return

        # Get active field and crop cycle
        field_result = await db.execute(
            select(Field).where(Field.farmer_id == farmer.id).limit(1)
        )
        field = field_result.scalar_one_or_none()
        if not field:
            await update.message.reply_text("⚠️ पहले /field और /crop पूरा करें।")
            return

        cycle_result = await db.execute(
            select(CropCycle)
            .where(CropCycle.field_id == field.id, CropCycle.is_active)
            .limit(1)
        )
        cycle = cycle_result.scalar_one_or_none()
        if not cycle:
            await update.message.reply_text("⚠️ पहले /field और /crop पूरा करें।")
            return

        import uuid
        observation = Observation(
            id=str(uuid.uuid4()),
            farmer_id=farmer.id,
            crop_cycle_id=cycle.id,
            field_id=field.id,
            observation_type="photo" if image_path else "text",
            text_content=text or None,
            image_path=image_path,
            reported_stage=cycle.current_stage,
        )
        db.add(observation)
        await db.commit()

        # Build agent context
        ctx = AgentContext(
            farmer_id=farmer.id,
            message=text or "crop photo attached",
            language="hi",
            crop_name=cycle.crop_name if cycle else state.get("crop_name"),
            crop_stage=cycle.current_stage if cycle else state.get("crop_stage"),
            field_id=field.id if field else state.get("field_id"),
            observation_id=observation.id,
            image_path=image_path,
            is_followup=False,
        )

        # Run agent
        agent = get_agent()
        try:
            response = await agent.process(db, ctx)
        except Exception as exc:
            logger.error(f"Agent processing failed: {exc}")
            await update.message.reply_text(
                "⚠️ कुछ त्रुटि हुई। कृपया पुनः प्रयास करें।\n"
                "An error occurred. Please try again.\n\n"
                "📞 किसान कॉल सेंटर: 1800-180-1551"
            )
            return

        # Build response with evidence and verifier info
        msg = response.display_text

        # Add evidence button if evidence exists
        keyboard = []
        if response.evidence_cards:
            keyboard.append([
                InlineKeyboardButton("📋 साक्ष्य / Evidence", callback_data="show_evidence"),
            ])
        if response.verifier_report and response.verifier_report.passes_all:
            keyboard.append([
                InlineKeyboardButton("✅ सत्यापित / Verified", callback_data="show_verifier"),
            ])
        elif response.verifier_report:
            keyboard.append([
                InlineKeyboardButton("⚠️ सुरक्षित सलाह / Safe Advice", callback_data="show_verifier"),
            ])

        # Add finance tracking button
        keyboard.append([
            InlineKeyboardButton("💰 खर्च जोड़ें / Add Expense", callback_data="cmd_finance"),
            InlineKeyboardButton("ℹ️ मदद / Help", callback_data="cmd_help"),
        ])

        msg += f"\n\n⚡ _{response.latency_ms}ms • {response.model_used} • {response.retrieval_path}_"

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(
            msg,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )

        # Store evidence for callback
        state["last_evidence"] = response.evidence_cards
        state["last_verifier"] = {
            "passes_all": response.verifier_report.passes_all if response.verifier_report else False,
            "details": str(response.verifier_report) if response.verifier_report else "",
        }


# ─── 7 Additional Slash Commands ──────────────────────────────────────

async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /calendar — show crop calendar for active cycle."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    if state.get("state") != "ready":
        await update.message.reply_text("⚠️ पहले /register, /field, और /crop करें।")
        return

    async with async_session_factory() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(CropCalendarTask).where(
                CropCalendarTask.cycle_id == state.get("crop_cycle_id", "")
            ).order_by(CropCalendarTask.days_from_sowing)
        )
        tasks = result.scalars().all()
        if not tasks:
            await update.message.reply_text("📅 कोई कैलेंडर कार्य नहीं। /crop से फसल दर्ज करें।")
            return
        lines = ["📅 *फसल कैलेंडर / Crop Calendar*"]
        for t in tasks:
            icon = "✅" if t.completed else "⬜"
            lines.append(f"{icon} [{t.stage}] {t.task_name}" + (f" — Day {t.days_from_sowing}" if t.days_from_sowing else ""))
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def prices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /prices — show mandi prices."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    crop = state.get("crop_name", "rice")
    district = "Munger"

    await update.message.send_chat_action(ChatAction.TYPING)
    from app.services.mandi import get_mandi_prices, get_msp
    prices = await get_mandi_prices(crop=crop, district=district)
    msp_data = await get_msp(crop=crop)

    lines = [f"🏪 *मंडी भाव / Mandi Prices — {crop.title()}*"]
    if prices.get("prices"):
        for p in prices["prices"]:
            latest = p["history"][0] if p["history"] else {}
            lines.append(f"  {p['type']}: ₹{latest.get('modal', 'N/A')}/{p.get('unit', 'quintal')}")
    if msp_data.get("msp_per_quintal"):
        lines.append(f"\n📊 *MSP 2025-26:* ₹{msp_data['msp_per_quintal']}/quintal")
    lines.append("\n_स्रोत: agmarknet.gov.in (सीडेड डेटा)_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def expense_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /expense — log a farming expense."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    if state.get("state") != "ready":
        await update.message.reply_text("⚠️ पहले /register, /field, और /crop करें।")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "💰 *खर्च दर्ज करें / Log Expense*\n\n"
            "फॉर्मेट: /expense <category> <amount> <description>\n"
            "उदाहरण: /expense fertilizer 500 \"DAP 50kg\"\n\n"
            "Categories: seed, fertilizer, pesticide, labour, irrigation, other",
            parse_mode="Markdown"
        )
        return
    # /expense <category> <amount> [description]
    category = args[0] if len(args) > 0 else "other"
    try:
        amount = float(args[1]) if len(args) > 1 else 0
    except ValueError:
        amount = 0
    desc = " ".join(args[2:]) if len(args) > 2 else ""

    if amount <= 0:
        await update.message.reply_text("⚠️ कृपया राशि लिखें। Example: /expense fertilizer 500")
        return

    async with async_session_factory() as db:
        from app.services.finance import log_expense
        result = await log_expense(
            db=db,
            farmer_id=state.get("farmer_id", ""),
            crop_cycle_id=state.get("crop_cycle_id", ""),
            category=category,
            amount=amount,
            description=desc,
        )
        await update.message.reply_text(
            f"✅ *खर्च दर्ज!*\n📝 {category}: ₹{amount}\n📋 {desc}\n🆔 {result['entry_id'][:8]}...",
            parse_mode="Markdown"
        )


async def sale_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sale — log harvest sale revenue."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    if state.get("state") != "ready":
        await update.message.reply_text("⚠️ पहले /register, /field, और /crop करें।")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "💵 *बिक्री दर्ज करें / Log Sale*\n\n"
            "फॉर्मेट: /sale <amount> <quantity_quintal> [buyer]\n"
            "उदाहरण: /sale 25000 10 \"Munger Mandi\"",
            parse_mode="Markdown"
        )
        return
    try:
        amount = float(args[0]) if len(args) > 0 else 0
    except ValueError:
        amount = 0
    qty = args[1] if len(args) > 1 else ""
    buyer = " ".join(args[2:]) if len(args) > 2 else ""

    if amount <= 0:
        await update.message.reply_text("⚠️ कृपया राशि लिखें। Example: /sale 25000 10")
        return

    async with async_session_factory() as db:
        from app.services.finance import log_revenue
        await log_revenue(
            db=db,
            farmer_id=state.get("farmer_id", ""),
            crop_cycle_id=state.get("crop_cycle_id", ""),
            category="harvest_sale",
            amount=amount,
            description=f"{qty} quintal to {buyer}".strip(),
        )
        await update.message.reply_text(
            f"✅ *बिक्री दर्ज!*\n💰 ₹{amount}\n📦 {qty} quintal\n🏪 {buyer}",
            parse_mode="Markdown"
        )


async def finance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /finance — show Profit & Loss for current cycle."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    if state.get("state") != "ready":
        await update.message.reply_text("⚠️ पहले /register, /field, और /crop करें।")
        return

    await update.message.send_chat_action(ChatAction.TYPING)
    async with async_session_factory() as db:
        from app.services.finance import compute_pnl
        pnl = await compute_pnl(
            db=db,
            farmer_id=state.get("farmer_id", ""),
            crop_cycle_id=state.get("crop_cycle_id", ""),
        )
        emoji = "🟢" if pnl["net_pnl"] >= 0 else "🔴"
        lines = [
            f"💰 *खेत का हिसाब / Farm P&L* {emoji}",
            "",
            f"📥 कुल आय / Revenue: ₹{pnl['total_revenue']:,.0f}",
            f"📤 कुल खर्च / Expenses: ₹{pnl['total_expenses']:,.0f}",
            f"📊 शुद्ध लाभ / Net P&L: ₹{pnl['net_pnl']:,.0f}",
            f"📈 लाभ दर / Margin: {pnl['profit_margin_pct']}%",
            f"📝 कुल प्रविष्टियां / Entries: {pnl['entry_count']}",
        ]
        if pnl.get("expenses_by_category"):
            lines.append("\n*खर्च विवरण:*")
            for cat, amt in pnl["expenses_by_category"].items():
                lines.append(f"  • {cat}: ₹{amt:,.0f}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /memory — show field history and patterns."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    if state.get("state") != "ready":
        await update.message.reply_text("⚠️ पहले /register, /field, और /crop करें।")
        return

    await update.message.send_chat_action(ChatAction.TYPING)
    async with async_session_factory() as db:
        from sqlalchemy import desc, select
        # Get recent observations
        result = await db.execute(
            select(Observation)
            .where(Observation.farmer_id == state.get("farmer_id", ""))
            .order_by(desc(Observation.created_at))
            .limit(10)
        )
        obs_list = result.scalars().all()
        if not obs_list:
            await update.message.reply_text("📋 अभी तक कोई प्रविष्टि नहीं। फोटो भेजें या समस्या लिखें।")
            return

        lines = ["🧠 *खेत की यादें / Field Memory*"]
        for obs in obs_list:
            date_str = obs.created_at.strftime("%d %b") if obs.created_at else "?"
            text = (obs.text_content or "[फोटो]")[:80]
            risk = obs.advisory.risk_level if obs.advisory else "?"
            lines.append(f"  📅 {date_str}: {text} → {risk}")

        # NDVI trend
        from app.models import SatelliteNDVI
        ndvi_result = await db.execute(
            select(SatelliteNDVI)
            .where(SatelliteNDVI.field_id == state.get("field_id", ""))
            .order_by(desc(SatelliteNDVI.date))
            .limit(4)
        )
        ndvi_vals = [r.ndvi_value for r in ndvi_result.scalars().all()]
        if ndvi_vals:
            trend = "📈" if len(ndvi_vals) > 1 and ndvi_vals[0] > ndvi_vals[-1] else "📉"
            lines.append(f"\n🛰️ *NDVI Trend:* {trend} {ndvi_vals[0]:.2f} (latest)")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def demo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /demo — bind a rich demo memory palace to this Telegram user."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    await update.message.send_chat_action(ChatAction.TYPING)
    await _activate_demo_memory(update.message, user_id, state)


async def _activate_demo_memory(message, user_id: str, state: dict):
    async with async_session_factory() as db:
        summary = await seed_demo_memory_palace(db, telegram_user_id=user_id)

    state["state"] = "ready"
    state["phone"] = user_id
    state["farmer_id"] = summary["farmer_id"]
    state["field_id"] = summary["field_id"]
    state["crop_cycle_id"] = summary["crop_cycle_id"]
    state["crop_name"] = summary["crop_name"]
    state["crop_stage"] = summary["crop_stage"]
    state["data"] = {
        "crop_name": summary["crop_name"],
        "crop_stage": summary["crop_stage"],
    }

    lines = [
        "⚡ *Demo Memory Palace Ready*",
        "",
        f"🌾 Crop: {summary['crop_name']} ({summary['crop_stage']})",
        f"🧠 Farm memories: {summary['observations']}",
        f"🛰️ NDVI points: {summary['ndvi_points']}",
        f"💰 Finance entries: {summary['finance_entries']}",
        "",
        "अब ये करके दिखाएं:",
        "• /memory — खेत की पुरानी यादें और NDVI",
        "• /prices — मंडी भाव और MSP",
        "• /finance — खर्च और P&L",
        "• लिखें: pattiyon pe brown spots hain",
        "• फोटो भेजें: Gemma 4 local vision path",
    ]
    await message.reply_text("\n".join(lines), parse_mode="Markdown")


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /health — system health check."""
    await update.message.send_chat_action(ChatAction.TYPING)
    import time
    t0 = time.perf_counter()

    from sqlalchemy import func, select

    from app.config import settings
    from app.database import async_session_factory
    from app.models import Farmer, WikiArticle

    async with async_session_factory() as db:
        farmer_count = (await db.execute(select(func.count(Farmer.id)))).scalar()
        advisory_count = (await db.execute(select(func.count(Advisory.id)))).scalar()
        wiki_count = (await db.execute(select(func.count(WikiArticle.id)))).scalar()
        pending = (await db.execute(
            select(func.count(AlertCluster.id)).where(AlertCluster.status == "pending")
        )).scalar()

    db_latency = int((time.perf_counter() - t0) * 1000)
    ollama_status = "⚠️ not reachable"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{settings.ollama_host}/api/tags")
            response.raise_for_status()
            models = [m.get("name", "") for m in response.json().get("models", [])]
            if any(settings.ollama_model in model for model in models):
                ollama_status = f"✅ {settings.ollama_model}"
            elif models:
                ollama_status = f"⚠️ model missing, available: {', '.join(models[:3])}"
            else:
                ollama_status = "⚠️ Ollama running, no models listed"
    except Exception as exc:
        logger.warning(f"Ollama health check failed: {exc}")

    lines = [
        "🏥 *AgriMesh V4.0 — System Health*",
        "",
        f"🤖 Model: {settings.ollama_model}",
        f"🧠 Local Ollama: {ollama_status}",
        f"📐 Grammar Decoding: {'✅ ON' if settings.use_grammar_decoding else '⚠️ OFF'}",
        f"🗄️ DB Latency: {db_latency}ms",
        f"👨‍🌾 Farmers: {farmer_count}",
        f"📝 Advisories: {advisory_count}",
        f"📚 Wiki Articles: {wiki_count}",
        f"🚨 Pending Clusters: {pending}",
        "",
        "🌐 API: http://localhost:8000",
        "📖 Docs: http://localhost:8000/docs",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─── Structured Data Capture (§6.7) ───────────────────────────────────

STRUCTURED_EXPENSE_RE = re.compile(
    r'(?:खर्च|expense|spent|paid|दिया|लागत)\s*[:\-]?\s*'
    r'(?:₹|Rs\.?|रुपये|रु\.?)?\s*(\d+(?:[,.]\d+)?)\s*'
    r'(?:रुपये|रु|rs|inr|₹)?',
    re.IGNORECASE
)

STRUCTURED_SALE_RE = re.compile(
    r'(?:बिक्री|sale|sold|बेचा|आमदनी|revenue)\s*[:\-]?\s*'
    r'(?:₹|Rs\.?|रुपये|रु\.?)?\s*(\d+(?:[,.]\d+)?)\s*'
    r'(?:रुपये|रु|rs|inr|₹)?',
    re.IGNORECASE
)

# "N bag X @ ₹Y" → expense
BAG_EXPENSE_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*(?:bag|बोरा|बोरी|sack|packet|pack|kg|किलो|quintal|क्विंटल)\s+'
    r'(\w+(?:\s+\w+)?)\s*(?:@|at|का|की|के|rate|भाव|₹|Rs\.?)\s*(\d+(?:[,.]\d+)?)',
    re.IGNORECASE
)

# "N quintal X @ ₹Y" → revenue
QUINTAL_SALE_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*(?:quintal|क्विंटल|क्विंटल)\s+'
    r'(\w+(?:\s+\w+)?)\s*(?:@|at|का|की|के|rate|भाव|₹|Rs\.?|बेचा|sold)\s*(\d+(?:[,.]\d+)?)',
    re.IGNORECASE
)


async def try_structured_capture(update: Update, user_id: str, text: str) -> bool:
    """
    §6.7: Attempt structured data capture from freeform text.
    Returns True if captured, False to fall through to agent.
    """
    state = get_user_state(user_id)

    # "N bag X @ ₹Y" → expense
    bag_match = BAG_EXPENSE_RE.search(text)
    if bag_match:
        qty = float(bag_match.group(1))
        item = bag_match.group(2).strip()
        try:
            rate = float(bag_match.group(3).replace(",", ""))
        except ValueError:
            rate = 0
        total = qty * rate if rate > 0 else qty

        if total > 0 and state.get("state") == "ready":
            async with async_session_factory() as db:
                from app.services.finance import log_expense
                category = _guess_expense_category(item)
                await log_expense(
                    db=db,
                    farmer_id=state.get("farmer_id", ""),
                    crop_cycle_id=state.get("crop_cycle_id", ""),
                    category=category,
                    amount=total,
                    description=f"{qty} {item} @ ₹{rate}",
                )
            await update.message.reply_text(
                f"✅ *खर्च अपने आप दर्ज!*\n📝 {qty} {item} @ ₹{rate} = ₹{total:,.0f}\n🏷️ {category}",
                parse_mode="Markdown"
            )
            return True

    # "N quintal X @ ₹Y" → revenue
    qtl_match = QUINTAL_SALE_RE.search(text)
    if qtl_match:
        qty = float(qtl_match.group(1))
        item = qtl_match.group(2).strip()
        try:
            rate = float(qtl_match.group(3).replace(",", ""))
        except ValueError:
            rate = 0
        total = qty * rate if rate > 0 else qty

        if total > 0 and state.get("state") == "ready":
            async with async_session_factory() as db:
                from app.services.finance import log_revenue
                await log_revenue(
                    db=db,
                    farmer_id=state.get("farmer_id", ""),
                    crop_cycle_id=state.get("crop_cycle_id", ""),
                    category="harvest_sale",
                    amount=total,
                    description=f"{qty} quintal {item} @ ₹{rate}",
                )
            await update.message.reply_text(
                f"✅ *बिक्री अपने आप दर्ज!*\n💰 {qty} क्विंटल {item} @ ₹{rate} = ₹{total:,.0f}",
                parse_mode="Markdown"
            )
            return True

    return False


def _guess_expense_category(item: str) -> str:
    item_lower = item.lower()
    if any(kw in item_lower for kw in ['seed', 'बीज']):
        return 'seed'
    if any(kw in item_lower for kw in ['fertilizer', 'urea', 'dap', 'npk', 'खाद', 'उर्वरक', 'यूरिया']):
        return 'fertilizer'
    if any(kw in item_lower for kw in ['pesticide', 'insecticide', 'fungicide', 'कीटनाशक', 'दवा']):
        return 'pesticide'
    if any(kw in item_lower for kw in ['labour', 'labor', 'मजदूर', 'मजदूरी']):
        return 'labour'
    if any(kw in item_lower for kw in ['irrigation', 'सिंचाई', 'पानी', 'diesel', 'डीजल']):
        return 'irrigation'
    return 'other'


# ─── Extended Commands: Profile, Multi-Field/Crop, Feedback ───────────

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /profile — view or set extended farmer profile."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    args = context.args

    if not args:
        # View profile
        async with async_session_factory() as db:
            phone = state.get("phone", user_id)
            result = await db.execute(select(Farmer).where(Farmer.phone == phone))
            farmer = result.scalar_one_or_none()
            if not farmer:
                await update.message.reply_text("⚠️ पहले /register करें।")
                return

            # Get extended profile
            from app.models_memory import FarmerProfile
            profile_result = await db.execute(
                select(FarmerProfile).where(FarmerProfile.farmer_id == farmer.id)
            )
            profile = profile_result.scalar_one_or_none()

            lines = [f"👤 *{farmer.name}* — {farmer.district}, {farmer.village or 'N/A'}"]
            lines.append(f"📞 {farmer.phone} | 🗣️ {farmer.preferred_language}")
            if profile:
                lines.append(f"\n🌿 खेत का आकार: {profile.farm_size_acres or '?'} एकड़")
                lines.append(f"💧 सिंचाई: {profile.irrigation_source or '?'} ({profile.water_reliability or '?'})")
                lines.append(f"🧪 मिट्टी परीक्षण: {profile.soil_test_status or '?'}")
                lines.append(f"🚜 उपकरण: {', '.join(profile.equipment_access or []) or 'नहीं'}")
                lines.append(f"💰 वार्षिक बजट: ₹{profile.annual_budget_rs or 0:,}")
                lines.append(f"📋 PM-KISAN: {'✅' if profile.pm_kisan_enrolled else '❌'} | PMFBY: {'✅' if profile.pmfby_enrolled else '❌'} | KCC: {'✅' if profile.kcc_holder else '❌'}")
                lines.append(f"\n_प्रोफाइल पूर्णता: {profile.profile_completeness:.0%}_")
            else:
                lines.append("\n⚠️ विस्तृत प्रोफाइल नहीं है। सेट करें: /profile set")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    elif args[0] == "set":
        state["state"] = "profiling"
        await update.message.reply_text(
            "📝 *प्रोफाइल सेटअप*\n\nकृपया बताएं:\n"
            "1. खेत का कुल आकार (एकड़ में)\n"
            "2. सिंचाई का स्रोत (canal/borewell/rainfed/drip)\n"
            "3. पानी की विश्वसनीयता (reliable/seasonal/unreliable)\n"
            "4. मिट्टी परीक्षण (done_recently/done_old/never)\n"
            "5. वार्षिक खेती बजट (₹ में)\n\n"
            "उदाहरण: 3 acre, borewell, seasonal, done_old, 50000",
            parse_mode="Markdown"
        )


async def fields_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /fields — list all registered fields."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    async with async_session_factory() as db:
        phone = state.get("phone", user_id)
        result = await db.execute(select(Farmer).where(Farmer.phone == phone))
        farmer = result.scalar_one_or_none()
        if not farmer:
            await update.message.reply_text("⚠️ पहले /register करें।")
            return

        field_result = await db.execute(
            select(Field).where(Field.farmer_id == farmer.id)
        )
        fields = field_result.scalars().all()
        if not fields:
            await update.message.reply_text("कोई खेत नहीं। /field से जोड़ें।")
            return

        active = state.get("field_id", "")
        lines = ["🌿 *आपके खेत / Your Fields*"]
        for f in fields:
            marker = " 🟢" if f.id == active else ""
            lines.append(f"{marker} • {f.name}: {f.area_acres} एकड़, {f.soil_type or '?'}")
        lines.append("\nसक्रिय खेत बदलें: /usefield <number>")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def usefield_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /usefield — switch active field."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    args = context.args

    async with async_session_factory() as db:
        phone = state.get("phone", user_id)
        result = await db.execute(select(Farmer).where(Farmer.phone == phone))
        farmer = result.scalar_one_or_none()
        if not farmer:
            await update.message.reply_text("⚠️ पहले /register करें।")
            return

        field_result = await db.execute(
            select(Field).where(Field.farmer_id == farmer.id)
        )
        fields = field_result.scalars().all()

        if args:
            try:
                idx = int(args[0]) - 1
                if 0 <= idx < len(fields):
                    state["field_id"] = fields[idx].id
                    await update.message.reply_text(f"✅ सक्रिय खेत: *{fields[idx].name}*", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"⚠️ 1-{len(fields)} के बीच संख्या चुनें।")
            except ValueError:
                # Try by name
                name = " ".join(args).lower()
                for f in fields:
                    if name in f.name.lower():
                        state["field_id"] = f.id
                        await update.message.reply_text(f"✅ सक्रिय खेत: *{f.name}*", parse_mode="Markdown")
                        return
                await update.message.reply_text("खेत नहीं मिला। /fields से सूची देखें।")
        else:
            await fields_command(update, context)


async def crops_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /crops — list crop cycles."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    async with async_session_factory() as db:
        phone = state.get("phone", user_id)
        result = await db.execute(select(Farmer).where(Farmer.phone == phone))
        farmer = result.scalar_one_or_none()
        if not farmer:
            await update.message.reply_text("⚠️ पहले /register करें।")
            return

        cycle_result = await db.execute(
            select(CropCycle, Field).join(Field, Field.id == CropCycle.field_id)
            .where(Field.farmer_id == farmer.id)
            .order_by(CropCycle.is_active.desc(), CropCycle.sowing_date.desc())
        )
        rows = cycle_result.all()
        if not rows:
            await update.message.reply_text("कोई फसल नहीं। /crop से जोड़ें।")
            return

        active_cycle = state.get("crop_cycle_id", "")
        lines = ["🌱 *आपकी फसलें / Your Crops*"]
        for cycle, field in rows:
            marker = " 🟢" if cycle.id == active_cycle else " ⬜"
            status = "सक्रिय" if cycle.is_active else "समाप्त"
            lines.append(f"{marker} [{status}] {cycle.crop_name} — {field.name} ({cycle.current_stage})")
        lines.append("\nसक्रिय फसल बदलें: /usecrop <number>")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def usecrop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /usecrop — switch active crop cycle."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    args = context.args

    async with async_session_factory() as db:
        phone = state.get("phone", user_id)
        result = await db.execute(select(Farmer).where(Farmer.phone == phone))
        farmer = result.scalar_one_or_none()
        if not farmer:
            await update.message.reply_text("⚠️ पहले /register करें।")
            return

        cycle_result = await db.execute(
            select(CropCycle, Field).join(Field).where(Field.farmer_id == farmer.id)
            .order_by(CropCycle.is_active.desc())
        )
        rows = cycle_result.all()

        if args:
            try:
                idx = int(args[0]) - 1
                if 0 <= idx < len(rows):
                    cycle, field = rows[idx]
                    state["crop_cycle_id"] = cycle.id
                    state["crop_name"] = cycle.crop_name
                    state["crop_stage"] = cycle.current_stage
                    state["field_id"] = field.id
                    await update.message.reply_text(
                        f"✅ सक्रिय फसल: *{cycle.crop_name}* ({field.name})",
                        parse_mode="Markdown"
                    )
                else:
                    await update.message.reply_text(f"⚠️ 1-{len(rows)} के बीच संख्या चुनें।")
            except ValueError:
                await update.message.reply_text("संख्या लिखें। /crops से सूची देखें।")
        else:
            await crops_command(update, context)


async def newcycle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /newcycle — start a new crop cycle on current field."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    args = context.args

    if not args:
        await update.message.reply_text(
            "🌱 *नई फसल / New Cycle*\n\n"
            "फॉर्मेट: /newcycle <crop_name> <variety> <stage>\n"
            "उदाहरण: /newcycle wheat HD3086 seedling\n"
            "Stages: pre_sowing, seedling, vegetative, flowering, fruiting, harvest",
            parse_mode="Markdown"
        )
        return

    crop_name = args[0]
    variety = args[1] if len(args) > 1 else ""
    stage = args[2] if len(args) > 2 else "pre_sowing"

    async with async_session_factory() as db:
        import uuid
        phone = state.get("phone", user_id)
        result = await db.execute(select(Farmer).where(Farmer.phone == phone))
        farmer = result.scalar_one_or_none()
        if not farmer:
            await update.message.reply_text("⚠️ पहले /register करें।")
            return

        cycle = CropCycle(
            id=str(uuid.uuid4()),
            field_id=state.get("field_id", ""),
            crop_name=crop_name,
            variety=variety,
            sowing_date=datetime.utcnow(),
            current_stage=stage,
            is_active=True,
        )
        db.add(cycle)
        await db.commit()

        state["crop_cycle_id"] = cycle.id
        state["crop_name"] = crop_name
        state["crop_stage"] = stage
        state["state"] = "ready"

        await update.message.reply_text(
            f"✅ *नई फसल शुरू!*\n🌱 {crop_name} ({variety}) — {stage} अवस्था",
            parse_mode="Markdown"
        )


async def closecycle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /closecycle — mark current crop cycle as complete."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    cycle_id = state.get("crop_cycle_id", "")
    if not cycle_id:
        await update.message.reply_text("कोई सक्रिय फसल नहीं।")
        return

    async with async_session_factory() as db:
        result = await db.execute(select(CropCycle).where(CropCycle.id == cycle_id))
        cycle = result.scalar_one_or_none()
        if cycle:
            cycle.is_active = False
            cycle.current_stage = "harvest"
            await db.commit()
            await update.message.reply_text(
                f"✅ *{cycle.crop_name}* फसल समाप्त (कटाई)। /newcycle से नई फसल शुरू करें।",
                parse_mode="Markdown"
            )
            state["crop_cycle_id"] = ""


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /tasks — show and manage crop calendar tasks."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    cycle_id = state.get("crop_cycle_id", "")
    if not cycle_id:
        await update.message.reply_text("कोई सक्रिय फसल नहीं।")
        return

    async with async_session_factory() as db:
        result = await db.execute(
            select(CropCalendarTask)
            .where(CropCalendarTask.cycle_id == cycle_id)
            .order_by(CropCalendarTask.days_from_sowing)
        )
        tasks = result.scalars().all()
        if not tasks:
            await update.message.reply_text("📅 कोई कार्य नहीं।")
            return

        pending = [t for t in tasks if not t.completed]
        done = [t for t in tasks if t.completed]
        lines = [f"📋 *कार्य / Tasks* ({len(done)}/{len(tasks)} पूर्ण)"]
        for t in pending[:5]:
            lines.append(f"  ⬜ [{t.stage}] {t.task_name}")
        if done:
            lines.append(f"\n✅ पूर्ण: {len(done)}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def why_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /why — explain why the last recommendation was given."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    last_evidence = state.get("last_evidence", [])
    last_verifier = state.get("last_verifier", {})

    if not last_evidence:
        await update.message.reply_text("अभी तक कोई सलाह नहीं दी गई। पहले फोटो भेजें या समस्या लिखें।")
        return

    lines = ["🔍 *सलाह का आधार / Why This Advice?*"]
    for card in last_evidence:
        src = card.get("source_name", "Unknown")
        trust = card.get("trust_level", "?")
        lines.append(f"\n📌 *{card.get('label', 'Evidence')}*")
        lines.append(f"   स्रोत: {src} (trust: {trust})")
    if last_verifier:
        passed = "✅ पास" if last_verifier.get("passes_all") else "⚠️ सुरक्षित फ़ॉलबैक"
        lines.append(f"\n🛡️ सत्यापन: {passed}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def sources_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sources — show source freshness dashboard."""
    await update.message.send_chat_action(ChatAction.TYPING)
    from app.services.evidence import check_source_freshness

    sources_to_check = [
        "AgriMesh Weather Tool (Seeded)",
        "AgriMesh Mandi Tool (Seeded)",
        "AgriMesh Graph-Wiki",
        "PM-KISAN — NIC Portal",
        "ICAR Kharif Agro-Advisories 2025",
    ]
    lines = ["📊 *स्रोत ताज़गी / Source Freshness*"]
    for src_name in sources_to_check:
        status = await check_source_freshness(src_name)
        icon = "🟢" if status["fresh"] else "🟡" if status["age_hours"] < status["ttl_hours"] * 2 else "🔴"
        lines.append(f"{icon} {src_name[:40]}... ({status['age_hours']}h / {status['ttl_hours']}h)")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /feedback — rate the last advisory (1-5)."""
    user_id = str(update.effective_user.id)
    args = context.args
    if not args:
        await update.message.reply_text(
            "⭐ *सलाह की रेटिंग / Rate Advice*\n\n"
            "/feedback 1-5 <optional comment>\n"
            "1 = बिल्कुल मदद नहीं | 5 = बहुत मददगार",
            parse_mode="Markdown"
        )
        return
    try:
        rating = int(args[0])
        if not 1 <= rating <= 5:
            raise ValueError
    except ValueError:
        await update.message.reply_text("1-5 के बीच संख्या दें।")
        return

    comment = " ".join(args[1:]) if len(args) > 1 else ""
    # Store feedback (future: persist to DB)
    state = get_user_state(user_id)
    state["last_feedback"] = {"rating": rating, "comment": comment}

    emoji = ["", "😞", "😐", "🙂", "😊", "🌟"][rating]
    await update.message.reply_text(f"{emoji} रेटिंग {rating}/5 दर्ज! धन्यवाद।")


async def outcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /outcome — report what happened after following advice."""
    user_id = str(update.effective_user.id)
    args = context.args
    if not args:
        await update.message.reply_text(
            "📝 *परिणाम रिपोर्ट / Outcome Report*\n\n"
            "/outcome <result> <comment>\n"
            "result: improved, no_change, worsened, not_tried\n"
            "उदाहरण: /outcome improved \"फफूंद कम हो गई\"",
            parse_mode="Markdown"
        )
        return

    result = args[0]
    comment = " ".join(args[1:]) if len(args) > 1 else ""
    valid_results = ["improved", "no_change", "worsened", "not_tried"]
    if result not in valid_results:
        await update.message.reply_text(f"⚠️ {', '.join(valid_results)} में से चुनें।")
        return

    state = get_user_state(user_id)
    state["last_outcome"] = {"result": result, "comment": comment}
    await update.message.reply_text(f"📝 परिणाम '{result}' दर्ज! सीखने के लिए धन्यवाद। 🌱")


# ─── Bot Runner ───────────────────────────────────────────────────────

def create_bot() -> Application:
    """Create and configure the Telegram bot application."""
    token = settings.telegram_bot_token
    if not token or token == "your_bot_token_here":
        logger.warning("TELEGRAM_BOT_TOKEN not set! Bot will not start.")
        return None

    app = Application.builder().token(token).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("demo", demo_command))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("field", field_command))
    app.add_handler(CommandHandler("fields", fields_command))
    app.add_handler(CommandHandler("usefield", usefield_command))
    app.add_handler(CommandHandler("crop", crop_command))
    app.add_handler(CommandHandler("crops", crops_command))
    app.add_handler(CommandHandler("usecrop", usecrop_command))
    app.add_handler(CommandHandler("newcycle", newcycle_command))
    app.add_handler(CommandHandler("closecycle", closecycle_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("calendar", calendar_command))
    app.add_handler(CommandHandler("prices", prices_command))
    app.add_handler(CommandHandler("expense", expense_command))
    app.add_handler(CommandHandler("sale", sale_command))
    app.add_handler(CommandHandler("finance", finance_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("why", why_command))
    app.add_handler(CommandHandler("sources", sources_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("outcome", outcome_command))
    app.add_handler(CommandHandler("health", health_command))

    # Messages (structured data capture runs first)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))

    return app


async def run_bot():
    """Start the Telegram bot inside an existing asyncio app."""
    app = create_bot()
    if app is None:
        logger.error("Bot token not configured. Set TELEGRAM_BOT_TOKEN in .env")
        return

    logger.info("Starting AgriMesh Telegram Bot...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def run_bot_polling():
    """Start the Telegram bot as a standalone process."""
    app = create_bot()
    if app is None:
        logger.error("Bot token not configured. Set TELEGRAM_BOT_TOKEN in .env")
        return
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run_bot_polling()
