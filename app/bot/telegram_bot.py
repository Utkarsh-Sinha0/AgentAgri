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
from contextlib import suppress
from pathlib import Path

from loguru import logger
from sqlalchemy import desc, func, select
from sqlalchemy import update as sa_update
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
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
from app.models_memory import ConversationThread, MemoryAtom
from app.services.agent import AgentContext, get_agent
from app.services.demo_seed import seed_demo_memory_palace
from app.services.voice import _normalize_lang, transcribe, voice_round_trip
from app.utils.security import hash_password
from app.utils.time import utc_now

# ─── Session storage (in-memory for demo; Redis in production) ────────

user_state: dict[str, dict] = {}  # telegram_id -> state dict

def get_user_state(user_id: str) -> dict:
    if user_id not in user_state:
        user_state[user_id] = {"state": "start", "data": {}}
    return user_state[user_id]


# ─── Shared copy / helpers ────────────────────────────────────────────


def _help_text() -> str:
    return (
        "ℹ️ *सहायता / Help*\n\n"
        "*शुरुआत / Getting started*\n"
        "/start — मुख्य मेन्यू और जारी बातचीत\n"
        "/demo — sample farm + memory load करें\n"
        "/register, /field, /crop — टेक्स्ट पंजीकरण\n"
        "🎙 आवाज़ भेजें — एक note में नाम+गाँव+ज़िला+फसल बोलें\n\n"
        "*रोज़ का काम / Daily*\n"
        "/prices — मंडी भाव + MSP\n"
        "/tasks, /calendar — आज क्या करना है\n"
        "/expense, /sale, /finance — पैसे का हिसाब\n"
        "/memory, /dashboard — खेत की पूरी तस्वीर\n\n"
        "*फसल चक्र / Crop cycle*\n"
        "/newcycle — नया cycle शुरू\n"
        "/closecycle — cycle बंद + अगली फसल का सुझाव\n"
        "/fields, /usefield, /crops, /usecrop — कई खेत/फसलें switch\n\n"
        "*बातचीत / Conversations*\n"
        "/threads — सभी बातचीत\n"
        "/newthread — नई बातचीत शुरू\n"
        "/endthread — मौजूदा बातचीत बंद\n\n"
        "*गोपनीयता / Privacy*\n"
        "/mydata — आपकी saved memory देखें\n"
        "/forgetme — raw memory redact करें (confirm माँगा जाएगा)\n"
        "/voice_lang hi-IN — आवाज़ की भाषा सेट करें\n\n"
        "*Trust*\n"
        "/why, /sources, /feedback, /outcome, /health\n\n"
        "फसल की समस्या लिखें, फोटो भेजें, या आवाज़ में बोलें।"
    )


async def _send_prices(message, state: dict):
    crop = state.get("crop_name", "rice")
    district = "Munger"
    await message.chat.send_action(ChatAction.TYPING)
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
    await message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _send_mydata(message, state: dict, user_id: str):
    farmer_id = await _resolve_farmer_id(state, user_id)
    if not farmer_id:
        await message.reply_text("पहले /register करें।")
        return
    async with async_session_factory() as db:
        atoms = (
            await db.execute(
                select(MemoryAtom)
                .where(MemoryAtom.farmer_id == farmer_id, MemoryAtom.redacted.is_(False))
                .order_by(desc(MemoryAtom.event_at))
                .limit(20)
            )
        ).scalars().all()
    if not atoms:
        await message.reply_text("आपके लिए अभी कोई saved memory नहीं है।")
        return
    lines = ["📦 *आपका डेटा / Your data*", "Server पर aggregate insights अलग रखे जाते हैं; यहाँ सिर्फ आपकी raw memory है."]
    for atom in atoms:
        day = atom.event_at.strftime("%Y-%m-%d") if atom.event_at else "?"
        lines.append(f"• {day} [{atom.atom_type}] {atom.summary[:120]}")
    lines.append("\n/forgetme आपकी raw memory redact करता है; aggregate anonymous summaries रह सकती हैं.")
    await message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─── Handlers ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    user_id = str(user.id)
    state = get_user_state(user_id)

    welcome = (
        "🌾 *AgriMesh* — आपका AI कृषि सलाहकार\n"
        "Your AI Agricultural Advisor\n\n"
        "मौसम, मंडी भाव, MSP, बीमारी पहचान, सरकारी योजनाएं और खेत का पूरा हिसाब — "
        "एक ही जगह। आवाज़ में बात करें, फोटो भेजें, या लिखें।\n\n"
        "Weather, mandi prices, MSP, disease ID, government schemes, and full farm "
        "accounting — all in one place. Speak, send a photo, or type.\n\n"
        "*शुरू करने के 3 तरीके / 3 ways to start:*\n"
        "🎙 आवाज़ भेजें — नाम, गाँव, ज़िला, फसल एक साथ बोलें (registration done)\n"
        "⚡ /demo — sample farm + memory अभी load करें\n"
        "📝 /register — टेक्स्ट में step-by-step पंजीकरण\n\n"
        "📞 किसान कॉल सेंटर: 1800-180-1551 — /help से सभी commands देखें"
    )

    try:
        async with async_session_factory() as db:
            farmer = await db.scalar(select(Farmer).where(Farmer.phone == user_id))
            if farmer:
                state["farmer_id"] = farmer.id
                has_thread = await db.scalar(
                    select(ConversationThread.id)
                    .where(
                        ConversationThread.farmer_id == farmer.id,
                        ConversationThread.channel == "telegram",
                        ConversationThread.is_active == True,  # noqa: E712
                    )
                    .limit(1)
                )
                if has_thread:
                    welcome += "\n\n🗂 आपकी पिछली बातचीत जारी है। /threads से देखें।"
    except Exception as exc:
        logger.warning(f"start-hint thread lookup failed: {exc}")

    keyboard = []
    if state.get("farmer_id"):
        keyboard.append([
            InlineKeyboardButton("▶ जारी रखें / Continue", callback_data="threads_list"),
            InlineKeyboardButton("✳ नई बातचीत / New", callback_data="thread_new"),
        ])
        keyboard.append([
            _dashboard_button("🧭 Dashboard / Map / Memory", phone=user_id),
        ])
        keyboard.append([
            InlineKeyboardButton("📸 Photo + Voice diagnose", callback_data="cmd_photo"),
            InlineKeyboardButton("💰 Prices & MSP", callback_data="cmd_prices"),
        ])
        keyboard.append([
            InlineKeyboardButton("📦 My data", callback_data="cmd_mydata"),
            InlineKeyboardButton("ℹ️ Help", callback_data="cmd_help"),
        ])
    else:
        keyboard.extend([
            [InlineKeyboardButton("⚡ Demo: sample farm + memory", callback_data="cmd_demo")],
            [InlineKeyboardButton("📝 Register: text step-by-step", callback_data="cmd_register")],
            [_dashboard_button("🧭 Dashboard preview", phone=user_id)],
            [InlineKeyboardButton("ℹ️ Help: see all commands", callback_data="cmd_help")],
        ])

    await update.message.reply_text(
        welcome,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    # First-time users: also send a Hindi voice prompt asking for the one-shot
    # registration note. Silent if voice pipeline disabled or Sarvam fails.
    if not state.get("farmer_id") and settings.enable_voice_pipeline:
        try:
            from app.services.voice import synthesize as _sarvam_tts

            prompt_hi = (
                "नमस्ते! अपना नाम, गाँव, ज़िला, और मुख्य फसल — एक ही आवाज़ संदेश में बताइए।"
            )
            audio = await _sarvam_tts(prompt_hi, target_lang="hi-IN", emotion="friendly")
            if audio:
                await context.bot.send_voice(chat_id=update.effective_chat.id, voice=audio)
        except Exception as exc:
            logger.warning(f"start voice prompt skipped: {exc}")

    state["state"] = "ready" if state.get("farmer_id") else "start"


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /register command."""
    user = update.effective_user
    user_id = str(user.id)
    state = get_user_state(user_id)
    state["state"] = "registering_name"

    await update.message.reply_text(
        "📝 *किसान पंजीकरण / Farmer Registration*\n\n"
        "आवाज़ में एक साथ बोल सकते हैं: नाम, गाँव, ज़िला, मुख्य फसल।\n"
        "या लिखकर शुरू करें — कृपया अपना पूरा नाम लिखें:\n"
        "You can speak name, village, district, main crop in one voice note, or type your full name:",
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
    elif current_state == "registering_tehsil":
        await _handle_tehsil_registration(update, user_id, text, state)
    elif current_state == "registering_village":
        await _handle_village_registration(update, user_id, text, state)
    elif current_state == "registering_field_name":
        await _handle_field_name(update, user_id, text, state)
    elif current_state == "registering_field_area":
        await _handle_field_area(update, user_id, text, state)
    elif current_state == "registering_field_soil":
        await _handle_field_soil(update, user_id, text, state)
    elif current_state == "registering_crop_name":
        await _handle_crop_name(update, user_id, text, state)
    elif current_state == "registering_crop_sowing":
        await _handle_crop_sowing(update, user_id, text, state)
    elif current_state == "registering_crop_stage":
        await _handle_crop_stage(update, user_id, text, state)
    elif current_state == "editing_field":
        await _apply_edit(update, user_id, text, state)
    elif current_state == "profiling":
        await _handle_profile_data(update, user_id, text, state)
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

    await update.message.reply_chat_action(ChatAction.TYPING)

    # Download photo
    photo_file = await update.message.photo[-1].get_file()
    photo_dir = Path(settings.data_dir) / "photos"
    photo_dir.mkdir(parents=True, exist_ok=True)
    photo_path = photo_dir / f"{user_id}_{utc_now().strftime('%Y%m%d_%H%M%S')}.jpg"
    await photo_file.download_to_drive(str(photo_path))

    caption = update.message.caption or ""
    if not caption.strip():
        state["pending_photo_path"] = str(photo_path)
        await update.message.reply_text(
            "📸 फोटो मिल गई। अब आवाज़ में अपना सवाल भेजिए, या टेक्स्ट में लक्षण लिखिए।\n"
            "Photo received. Now send your question by voice, or type the symptoms."
        )
        return

    await _process_farmer_query(update, user_id, caption, str(photo_path))


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Voice round-trip via Sarvam: STT -> agent -> TTS reply.

    When settings.enable_voice_pipeline is False, falls back to persist-only
    behavior (legacy) so the bot remains usable without Sarvam credentials.
    """
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)

    voice = update.message.voice or update.message.audio
    if voice is None:
        return

    # Route to registration only if the farmer is genuinely unregistered.
    # Checking the DB instead of the in-memory state string protects against
    # state drift (e.g. /register sets state to 'registering_name' but voice
    # registration succeeded out-of-band, or /start leaves state at 'start').
    if state.get("state") != "ready":
        async with async_session_factory() as _db:
            _farmer_exists = await _db.scalar(
                select(Farmer.id).where(Farmer.phone == state.get("phone", user_id))
            )
        if not _farmer_exists:
            await _handle_voice_registration(update, context, user_id, state, voice)
            return
        # Farmer is registered; treat this voice as a normal turn and fix state.
        state["state"] = "ready"

    await update.message.chat.send_action(ChatAction.TYPING)

    voice_dir = Path(settings.data_dir) / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    voice_path = voice_dir / f"{user_id}_{utc_now().strftime('%Y%m%d_%H%M%S')}.ogg"

    try:
        tg_file = await voice.get_file()
        await tg_file.download_to_drive(str(voice_path))
    except Exception as exc:
        logger.error(f"Voice download failed: {exc}")
        await update.message.reply_text("❌ Audio download failed. Please try again.")
        return

    # Persist as Observation regardless of pipeline flag.
    farmer_id_for_agent: str | None = None
    field_id_for_agent: str | None = None
    cycle_id_for_agent: str | None = None
    crop_name: str | None = None
    crop_stage: str | None = None
    preferred_lang: str = settings.voice_default_target_lang

    pending_photo_path = state.pop("pending_photo_path", None)

    async with async_session_factory() as db:
        phone = state.get("phone", user_id)
        farmer = await db.scalar(select(Farmer).where(Farmer.phone == phone))
        if farmer is None:
            await update.message.reply_text("⚠️ पहले /register करें।")
            return
        farmer_id_for_agent = farmer.id
        preferred_lang = getattr(farmer, "preferred_language", None) or preferred_lang

        # State is in-memory and is wiped on bot restart. Rehydrate the
        # farmer's active field + cycle from the DB so observations always
        # have non-null FKs.
        if not state.get("crop_cycle_id") or not state.get("field_id"):
            _field = await db.scalar(
                select(Field).where(Field.farmer_id == farmer.id).order_by(Field.id).limit(1)
            )
            if _field is not None:
                state["field_id"] = _field.id
                _cycle = await db.scalar(
                    select(CropCycle)
                    .where(CropCycle.field_id == _field.id, CropCycle.is_active.is_(True))
                    .order_by(CropCycle.sowing_date.desc())
                    .limit(1)
                )
                if _cycle is not None:
                    state["crop_cycle_id"] = _cycle.id
                    state.setdefault("crop_name", _cycle.crop_name)
                    state.setdefault("crop_stage", _cycle.current_stage)

        import uuid
        obs = Observation(
            id=str(uuid.uuid4()),
            farmer_id=farmer.id,
            crop_cycle_id=state.get("crop_cycle_id"),
            field_id=state.get("field_id"),
            observation_type="photo_voice" if pending_photo_path else "voice",
            text_content=update.message.caption or "[voice note]",
            image_path=pending_photo_path,
            audio_path=str(voice_path),
        )
        db.add(obs)
        try:
            await db.commit()
            state["last_observation_id"] = obs.id
        except Exception as exc:
            await db.rollback()
            logger.error(f"Voice observation persist failed: {exc}")
            await update.message.reply_text("❌ Save failed. Please try again.")
            return

        field_id_for_agent = state.get("field_id")
        cycle_id_for_agent = state.get("crop_cycle_id")
        crop_name = state.get("crop_name")
        crop_stage = state.get("crop_stage")

    if not settings.enable_voice_pipeline:
        await update.message.reply_text(
            "🎙️ आवाज़ संदेश मिल गया! जल्द ही प्रसंस्करण होगा।\n"
            "Voice note received — transcription will be processed shortly."
        )
        return

    # Voice pipeline: STT -> en -> agent -> target -> TTS
    # Run STT first so we can persist the real transcript before agent reasoning.
    transcript_seed = ""
    try:
        stt_result = await transcribe(voice_path, source_lang=_normalize_lang(preferred_lang))
        transcript_seed = stt_result.get("text", "") or ""
        if transcript_seed:
            async with async_session_factory() as db_up:
                await db_up.execute(
                    sa_update(Observation)
                    .where(Observation.id == state["last_observation_id"])
                    .values(text_content=transcript_seed)
                )
                await db_up.commit()
    except Exception as exc:
        logger.warning(f"Pre-STT transcribe failed (will let round-trip retry): {exc}")

    # Guard: empty or trivial transcript — don't waste an agent turn replying
    # with a cached MSP. Tell the farmer we didn't hear them and bail.
    if len(transcript_seed.strip().split()) < 2:
        await update.message.reply_text(
            "🎙 आवाज़ साफ़ नहीं सुनाई दी। कृपया दोबारा बोलिए।\n"
            "Couldn't catch that clearly — please record again."
        )
        return

    # Heuristic: a new voice note is rarely a literal follow-up to the prior
    # advisory. Only treat as a follow-up if the farmer used a follow-up cue
    # in the transcript ("aapne kaha", "जैसा आपने कहा", "earlier", etc.),
    # otherwise the agent keeps mutating the old advisory ("Risk: X → Y").
    followup_cues = (
        "aapne kaha", "jaisa kaha", "earlier", "pichhli", "पिछली", "जैसा आपने कहा",
        "आपने कहा", "follow up", "follow-up", "as you said",
    )
    is_followup = any(cue in transcript_seed.lower() for cue in followup_cues)
    prev_advisory_id = state.get("last_advisory_id") if is_followup else None

    async def _agent_call(prompt_en: str) -> str:
        async with async_session_factory() as db2:
            ctx = AgentContext(
                farmer_id=farmer_id_for_agent,
                message=prompt_en,
                language="en",
                crop_name=crop_name,
                crop_stage=crop_stage,
                field_id=field_id_for_agent,
                crop_cycle_id=cycle_id_for_agent,
                observation_id=state.get("last_observation_id"),
                image_path=pending_photo_path,
                is_followup=is_followup,
                previous_advisory_id=prev_advisory_id,
            )
            agent = get_agent()
            response = await agent.process(db2, ctx)
            return response.display_text

    try:
        result = await voice_round_trip(
            audio_path=voice_path,
            agent_call=_agent_call,
            target_lang_hint=preferred_lang,
        )
    except Exception as exc:
        logger.error(f"Voice round-trip failed: {exc}")
        await update.message.reply_text(
            "❌ Voice processing failed. Please type your question or try again."
        )
        return

    if result.transcript:
        await update.message.reply_text(f"📝 आपने कहा / You said: {result.transcript}")

    if result.reply_audio_bytes:
        try:
            await context.bot.send_voice(
                chat_id=update.effective_chat.id,
                voice=result.reply_audio_bytes,
            )
        except Exception as exc:
            logger.warning(f"send_voice failed: {exc}")

    await update.message.reply_text(result.reply_text_target or result.reply_text_en)


async def _handle_voice_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str, state: dict, voice):
    """One-note onboarding: STT -> translate -> extract -> upsert farmer/field/cycle."""
    if not settings.enable_voice_pipeline:
        await update.message.reply_text(
            "🎙️ आवाज़ पंजीकरण के लिए Sarvam voice pipeline चालू नहीं है। /register से टेक्स्ट पंजीकरण करें।"
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    voice_dir = Path(settings.data_dir) / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    voice_path = voice_dir / f"{user_id}_registration_{utc_now().strftime('%Y%m%d_%H%M%S')}.ogg"
    try:
        tg_file = await voice.get_file()
        await tg_file.download_to_drive(str(voice_path))
    except Exception as exc:
        logger.error(f"Voice registration download failed: {exc}")
        await update.message.reply_text("❌ आवाज़ डाउनलोड नहीं हो सकी। फिर कोशिश करें।")
        return

    try:
        from app.services.voice import (
            extract_registration_fields,
            synthesize,
            transcribe,
            translate,
        )

        stt = await transcribe(voice_path)
        detected_lang = stt.get("detected_lang") or settings.voice_default_target_lang
        transcript = stt.get("text") or ""
        if len(transcript.strip().split()) < 3:
            await update.message.reply_text(
                "🎙 आवाज़ बहुत छोटी या खाली थी। कृपया एक साथ बोलें: नाम, गाँव, ज़िला, मुख्य फसल।\n"
                "Voice was empty/too short. Please say: name, village, district, main crop."
            )
            return
        transcript_en = await translate(transcript, detected_lang, "en-IN")
        fields = await extract_registration_fields(transcript_en)
        preferred_lang = fields.get("preferred_lang") or detected_lang
    except Exception as exc:
        logger.error(f"Voice registration pipeline failed: {exc}")
        await update.message.reply_text("❌ आवाज़ समझ नहीं आई। कृपया /register से टेक्स्ट में करें या फिर से बोलें।")
        return

    if not fields.get("extraction_ok"):
        await update.message.reply_text(
            f"❓ मैं समझ नहीं पाया। आपने कहा: \"{transcript[:200]}\"\n"
            "कृपया फिर से बोलें — एक साथ अपना नाम, गाँव, ज़िला, और मुख्य फसल बताएं।\n"
            "Couldn't extract your details. Please re-record with name, village, district, and main crop, "
            "or use /register for text step-by-step."
        )
        return

    import uuid
    async with async_session_factory() as db:
        farmer = await db.scalar(select(Farmer).where(Farmer.phone == user_id))
        if farmer is None:
            farmer = Farmer(
                id=str(uuid.uuid4()),
                phone=user_id,
                hashed_password=hash_password(f"telegram:{user_id}"),
                name=fields["name"],
                preferred_language=preferred_lang,
                district=fields["district"],
                tehsil=fields.get("tehsil") or "",
                village=fields.get("village") or "",
            )
            db.add(farmer)
        else:
            farmer.name = fields["name"]
            farmer.preferred_language = preferred_lang
            farmer.district = fields["district"]
            farmer.tehsil = fields.get("tehsil") or farmer.tehsil
            farmer.village = fields.get("village") or farmer.village
            farmer.is_active = True

        field = Field(
            id=str(uuid.uuid4()),
            farmer_id=farmer.id,
            name=fields.get("village") or "Main field",
            area_acres=fields.get("field_area_acres") or 1.0,
            soil_type=fields.get("soil_type") or "loam",
            irrigation_type="unknown",
        )
        db.add(field)
        cycle = CropCycle(
            id=str(uuid.uuid4()),
            field_id=field.id,
            crop_name=fields["primary_crop"],
            sowing_date=utc_now(),
            current_stage="pre_sowing",
            is_active=True,
        )
        db.add(cycle)
        await db.commit()

    state.update({
        "state": "ready",
        "farmer_id": farmer.id,
        "phone": user_id,
        "field_id": field.id,
        "crop_cycle_id": cycle.id,
        "crop_name": cycle.crop_name,
        "crop_stage": cycle.current_stage,
    })
    msg = (
        f"✅ पंजीकरण पूरा: {farmer.name}, {farmer.village or '-'}, {farmer.district}; "
        f"फसल: {cycle.crop_name}. अब फोटो, टेक्स्ट या आवाज़ से सवाल पूछें।"
    )
    try:
        audio = await synthesize(msg, preferred_lang)
        if audio:
            await context.bot.send_voice(chat_id=update.effective_chat.id, voice=audio)
    except Exception as exc:
        logger.warning(f"voice registration TTS failed: {exc}")
    await update.message.reply_text(msg)


async def cmd_voice_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/voice_lang <code> - set the farmer's preferred TTS language (e.g., hi-IN, bho, ta-IN)."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /voice_lang <code>\n"
            "Examples: hi-IN, bn-IN, ta-IN, mr-IN, te-IN, pa-IN, gu-IN, kn-IN, ml-IN, od-IN, en-IN"
        )
        return
    code = args[0].strip()
    async with async_session_factory() as db:
        phone = state.get("phone", user_id)
        farmer = await db.scalar(select(Farmer).where(Farmer.phone == phone))
        if farmer is None:
            await update.message.reply_text("⚠️ पहले /register करें।")
            return
        farmer.preferred_language = code
        await db.commit()
    await update.message.reply_text(f"✅ Voice language set to {code}.")


REPLY_MODES = {"text", "both", "voice"}
RISK_TO_EMOTION = {
    "NORMAL": "friendly",
    "WATCH": "concerned",
    "PREVENTIVE_ACTION": "urgent",
    "ESCALATE": "firm",
}


async def cmd_voice_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/voice_reply <text|both|voice> — choose how the bot replies.

    text  — text only (default)
    both  — text + voice note (with emotion shaped by risk level)
    voice — voice note only
    """
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    args = context.args or []
    if not args or args[0].strip().lower() not in REPLY_MODES:
        current = state.get("reply_mode", "text")
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📝 Text", callback_data="reply_mode:text"),
            InlineKeyboardButton("📝+🔊 Both", callback_data="reply_mode:both"),
            InlineKeyboardButton("🔊 Voice", callback_data="reply_mode:voice"),
        ]])
        await update.message.reply_text(
            f"जवाब कैसे चाहिए? Current: *{current}*\n"
            "How do you want replies? Pick one:",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return
    mode = args[0].strip().lower()
    state["reply_mode"] = mode
    await update.message.reply_text(f"✅ Reply mode: {mode}")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks."""
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest as exc:
        if "Query is too old" not in str(exc):
            raise
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
        await query.message.reply_text(_help_text(), parse_mode="Markdown")
    elif data == "cmd_prices":
        await _send_prices(query.message, state)
    elif data == "cmd_mydata":
        await _send_mydata(query.message, state, user_id)
    elif data == "cmd_dashboard":
        farmer_id = state.get("farmer_id")
        if not farmer_id:
            async with async_session_factory() as db:
                farmer = await db.scalar(select(Farmer).where(Farmer.phone == user_id))
                farmer_id = farmer.id if farmer else ""
        url = _dashboard_url(farmer_id=farmer_id, phone=user_id if not farmer_id else None)
        is_public = url.startswith("https://") and "localhost" not in url and "127.0.0.1" not in url
        if is_public:
            await query.message.reply_text(
                "🧭 *AgriMesh Dashboard*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Open dashboard", url=url)]]),
            )
        else:
            await query.message.reply_text(
                f"🧭 *Dashboard (dev mode)*\n\nLocal URL: `{url}`\n"
                "Open it in a browser on this machine — Telegram won't render an "
                "inline button for non-https URLs.",
                parse_mode="Markdown",
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
    elif data == "threads_list":
        await _show_threads(update, user_id, show_archived=False)
    elif data == "threads_show_archived":
        await _show_threads(update, user_id, show_archived=True)
    elif data.startswith("thread_switch:"):
        thread_id = data.split(":", 1)[1]
        async with async_session_factory() as db:
            thread = await db.get(ConversationThread, thread_id)
            farmer_id = await _resolve_farmer_id(state, user_id)
            if thread and thread.farmer_id == farmer_id:
                state["field_id"] = thread.field_id
                state["crop_cycle_id"] = thread.crop_cycle_id
                state["last_advisory_id"] = thread.last_advisory_id
                label = thread.title or f"Thread {thread.id[:8]}"
                await query.edit_message_text(f"✅ बातचीत पर जाएँ / Switched to {label}.")
            else:
                await query.edit_message_text("❌ बातचीत नहीं मिली / Thread not found.")
    elif data == "thread_new":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("हाँ / Yes, close it", callback_data="newthread_confirm")],
            [InlineKeyboardButton("नहीं / Cancel", callback_data="newthread_cancel")],
        ])
        await query.message.reply_text(
            "इस बातचीत को बंद करें और नई शुरू करें? / Close this conversation and start fresh?",
            reply_markup=keyboard,
        )
    elif data == "newthread_confirm":
        await _archive_active_thread(user_id, state)
        try:
            await query.edit_message_text(
                "✅ बातचीत बंद। नया सवाल पूछें / Conversation closed. Ask a new question."
            )
        except BadRequest:
            await query.message.reply_text(
                "✅ बातचीत बंद। नया सवाल पूछें / Conversation closed. Ask a new question."
            )
    elif data == "newthread_cancel":
        try:
            await query.edit_message_text("❌ रद्द / Cancelled.")
        except BadRequest:
            await query.message.reply_text("❌ रद्द / Cancelled.")
    elif data == "forgetme_confirm":
        with suppress(BadRequest):
            await query.edit_message_reply_markup(reply_markup=None)
        await _execute_forgetme(query.message, state, user_id)
    elif data == "forgetme_cancel":
        try:
            await query.edit_message_text("❌ रद्द / Cancelled — आपकी memory सुरक्षित है।")
        except BadRequest:
            await query.message.reply_text("❌ रद्द / Cancelled — आपकी memory सुरक्षित है।")
    elif data.startswith("edit_field:"):
        field_key = data.split(":", 1)[1]
        if field_key in EDIT_FIELDS:
            state.setdefault("data", {})["edit_field"] = field_key
            state["state"] = "editing_field"
            prompt = EDIT_FIELDS[field_key][1]
            try:
                await query.edit_message_text(prompt)
            except BadRequest:
                await query.message.reply_text(prompt)
    elif data == "edit_cancel":
        try:
            await query.edit_message_text("❌ रद्द / Cancelled.")
        except BadRequest:
            await query.message.reply_text("❌ रद्द / Cancelled.")
    elif data.startswith("reply_mode:"):
        mode = data.split(":", 1)[1]
        if mode in REPLY_MODES:
            state["reply_mode"] = mode
            label = {"text": "📝 Text only", "both": "📝+🔊 Text + Voice", "voice": "🔊 Voice only"}[mode]
            try:
                await query.edit_message_text(f"✅ Reply mode set: {label}")
            except BadRequest:
                await query.message.reply_text(f"✅ Reply mode set: {label}")


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
    state["state"] = "registering_tehsil"
    await update.message.reply_text(
        "तहसील / ब्लॉक का नाम लिखें / Enter your tehsil / block:"
    )


async def _handle_tehsil_registration(update, user_id: str, tehsil: str, state: dict):
    state["data"]["tehsil"] = tehsil
    state["state"] = "registering_village"
    await update.message.reply_text(
        "गाँव का नाम लिखें / Enter your village:"
    )


async def _handle_village_registration(update, user_id: str, village: str, state: dict):
    state["data"]["village"] = village

    district = state["data"].get("district", "")
    tehsil = state["data"].get("tehsil", "")

    async with async_session_factory() as db:
        import uuid
        existing = await db.execute(select(Farmer).where(Farmer.phone == user_id))
        farmer = existing.scalar_one_or_none()
        if farmer:
            farmer.name = state["data"]["name"]
            farmer.preferred_language = "hi"
            farmer.district = district
            farmer.tehsil = tehsil
            farmer.village = village
            farmer.is_active = True
        else:
            farmer = Farmer(
                id=str(uuid.uuid4()),
                phone=user_id,
                hashed_password=hash_password(f"telegram:{user_id}"),
                name=state["data"]["name"],
                preferred_language="hi",
                district=district,
                tehsil=tehsil,
                village=village,
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
    state["state"] = "registering_crop_sowing"
    await update.message.reply_text(
        "बुवाई की तारीख लिखें (YYYY-MM-DD) / Sowing date (YYYY-MM-DD).\n"
        "अगर पता न हो, लिखें 'skip' / Type 'skip' if unknown:"
    )


def _state_sowing_date(state: dict):
    """Return stored sowing_date or utc_now() fallback."""
    from datetime import datetime
    raw = state.get("data", {}).get("sowing_date")
    if not raw:
        return utc_now()
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return utc_now()


def _parse_sowing_date(text: str):
    """Parse a YYYY-MM-DD sowing date; return None on skip / invalid."""
    from datetime import datetime
    text = (text or "").strip().lower()
    if text in ("skip", "छोड़ें", "छोडें", "-", ""):
        return None
    try:
        dt = datetime.strptime(text, "%Y-%m-%d")
        # Future-dated sowings are almost certainly typos; reject.
        if dt > utc_now().replace(tzinfo=None):
            return None
        return dt
    except ValueError:
        return None


async def _handle_crop_sowing(update, user_id: str, text: str, state: dict):
    parsed = _parse_sowing_date(text)
    if parsed is None and text.strip().lower() not in ("skip", "छोड़ें", "छोडें", "-", ""):
        await update.message.reply_text(
            "⚠️ कृपया YYYY-MM-DD में लिखें (जैसे 2026-04-15) या 'skip' लिखें।\n"
            "Please enter YYYY-MM-DD (e.g. 2026-04-15) or 'skip'."
        )
        return
    state["data"]["sowing_date"] = parsed.isoformat() if parsed else None
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
            sowing_date=_state_sowing_date(state),
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
            sowing_date=_state_sowing_date(state),
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

    await update.message.reply_chat_action(ChatAction.TYPING)

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

        stage_notice = ""
        try:
            from app.services.crop_cycle import advance_stage, stage_message_hi

            new_stage = await advance_stage(db, cycle)
            if new_stage:
                stage_notice = stage_message_hi(cycle, new_stage) + "\n\n"
        except Exception as exc:
            logger.warning(f"stage advance skipped: {exc}")

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
            crop_cycle_id=cycle.id if cycle else state.get("crop_cycle_id"),
            observation_id=observation.id,
            image_path=image_path,
            is_followup=bool(state.get("last_advisory_id")),
            # E3 needs both flags to render "what changed" against the prior
            # advisory; passing only is_followup made the feature silently dead.
            previous_advisory_id=state.get("last_advisory_id"),
        )

        # Run agent
        agent = get_agent()
        try:
            response = await agent.process(db, ctx)
        except Exception as exc:
            logger.exception(f"Agent processing failed: {exc}")
            await update.message.reply_text(
                "An error occurred. Please try again.\n"
                "कुछ त्रुटि हुई। कृपया पुनः प्रयास करें।\n\n"
                "Kisan Call Center: 1800-180-1551"
            )
            return

        # Build response with evidence and verifier info
        msg = stage_notice + response.display_text

        # Prepend an outbreak warning banner once, if this farmer has a
        # pending alert that has not yet been surfaced in chat.
        try:
            from app.services.outbreak import (
                fetch_pending_outbreak_for_farmer,
                format_warning_message,
                mark_outbreak_consumed,
            )
            async with async_session_factory() as _alert_db:
                pending = await fetch_pending_outbreak_for_farmer(_alert_db, farmer.id)
                if pending is not None:
                    msg = f"{format_warning_message(pending)}\n\n{msg}"
                    await mark_outbreak_consumed(_alert_db, pending, farmer.id)
        except Exception as exc:
            logger.warning(f"outbreak prepend skipped: {exc}")

        # Add evidence button if evidence exists
        keyboard = []
        if response.evidence_cards:
            keyboard.append([
                InlineKeyboardButton("📋 साक्ष्य / Evidence", callback_data="show_evidence"),
            ])
        keyboard.append([
            _dashboard_button("🧭 Personal dashboard / खेत पेज", farmer_id=farmer.id)
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

        # Conversation thread row (design §4.2 / §8)
        keyboard.append([
            InlineKeyboardButton("🗂 बातचीत / Threads", callback_data="threads_list"),
            InlineKeyboardButton("✳ नई बातचीत / New thread", callback_data="thread_new"),
        ])

        msg += f"\n\n---\n{response.latency_ms}ms | {response.model_used} | {response.retrieval_path}"

        # Bilingual reply policy:
        # - text always contains English.
        # - if detected/preferred is English -> English ONLY.
        # - else -> English block first, then native block separated by "———".
        # - audio uses native (detected wins; stored pref is fallback).
        pref_lang = (getattr(farmer, "preferred_language", "") or "").lower()
        typed_text = text or ""
        has_devanagari = any("ऀ" <= c <= "ॿ" for c in typed_text)
        has_ascii_letters = any("a" <= c.lower() <= "z" for c in typed_text)
        if has_devanagari and not has_ascii_letters:
            detected_lang = "hi-IN"
        elif has_ascii_letters and not has_devanagari:
            detected_lang = "en-IN"
        else:
            detected_lang = ""  # inconclusive

        native_lang = detected_lang if (detected_lang and not detected_lang.startswith("en")) else (pref_lang if pref_lang and not pref_lang.startswith("en") else "")
        english_only = (detected_lang.startswith("en")) or (not native_lang)

        if settings.enable_voice_pipeline:
            try:
                from app.services.voice import translate as _sarvam_translate

                msg_en = await _sarvam_translate(msg, source_lang="hi-IN", target_lang="en-IN")
                if english_only:
                    msg = msg_en
                else:
                    msg_native = msg if native_lang.startswith("hi") else await _sarvam_translate(
                        msg, source_lang="hi-IN", target_lang=native_lang
                    )
                    msg = f"{msg_en}\n———\n{msg_native}"
            except Exception as exc:
                logger.warning(f"bilingual render skipped ({detected_lang}/{native_lang}): {exc}")
        effective_lang = "en-IN" if english_only else (native_lang or "hi-IN")

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        # Default: text + audio for every reply (audio in the conversation's
        # detected language). User can override with /replymode.
        reply_mode = state.get("reply_mode") or "both"
        send_text = reply_mode in {"text", "both"}
        send_audio = reply_mode in {"both", "voice"} and settings.enable_voice_pipeline

        # Agent output is templated text + LLM contextualization; an
        # unbalanced * or _ from the LLM trips Telegram's Markdown parser
        # and raises BadRequest. Fall back to plain text rather than
        # dropping the advisory on the floor — keyboard still attaches.
        if send_text:
            try:
                await update.message.reply_text(
                    msg,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )
            except BadRequest as e:
                if "can't parse entities" in str(e).lower() or "parse" in str(e).lower():
                    await update.message.reply_text(msg, reply_markup=reply_markup)
                else:
                    raise

        if send_audio:
            try:
                from app.services.voice import synthesize as _sarvam_tts

                tts_lang = effective_lang if effective_lang else (pref_lang or "hi-IN")
                emotion = RISK_TO_EMOTION.get(response.risk_level, "friendly")
                # When msg is bilingual (English ——— native), speak only the
                # native half. When English-only, speak the whole thing.
                tts_source = msg.split("\n———\n", 1)[1] if "\n———\n" in msg else msg
                tts_text = re.sub(r"[*_`#─]+", "", tts_source)
                tts_text = re.sub(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]", "", tts_text).strip()
                audio_bytes = await _sarvam_tts(tts_text, target_lang=tts_lang, emotion=emotion)
                if audio_bytes:
                    await update.message.reply_voice(
                        voice=audio_bytes,
                        reply_markup=reply_markup if not send_text else None,
                    )
            except Exception as exc:
                logger.warning(f"audio reply skipped: {exc}")
                if not send_text:
                    await update.message.reply_text(msg, reply_markup=reply_markup)

        # Store evidence for callback
        state["last_evidence"] = response.evidence_cards
        state["last_advisory_id"] = response.advisory_id
        state["last_observation_id"] = observation.id
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
    await _send_prices(update.message, state)


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

    await update.message.reply_chat_action(ChatAction.TYPING)
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

    await update.message.reply_chat_action(ChatAction.TYPING)
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


async def mydata_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mydata — farmer-visible personal data export."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    await _send_mydata(update.message, state, user_id)


async def forgetme_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /forgetme — ask confirm before soft-redacting farmer-owned atoms."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    farmer_id = await _resolve_farmer_id(state, user_id)
    if not farmer_id:
        await update.message.reply_text("पहले /register करें।")
        return
    async with async_session_factory() as db:
        count = await db.scalar(
            select(func.count(MemoryAtom.id)).where(  # type: ignore[arg-type]
                MemoryAtom.farmer_id == farmer_id,
                MemoryAtom.redacted.is_(False),
            )
        )
    count = int(count or 0)
    if count == 0:
        await update.message.reply_text(
            "आपकी अभी कोई conversation memory save नहीं है — कुछ redact करने को नहीं है।\n"
            "(आपके खेत, फसल और खर्च records अलग हैं — वे यथावत रहेंगे।)\n\n"
            "No conversation memory stored yet — nothing to redact.\n"
            "(Your field, crop, and expense records are separate and remain intact.)"
        )
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"हाँ / Yes — redact {count}", callback_data="forgetme_confirm"),
        InlineKeyboardButton("नहीं / Cancel", callback_data="forgetme_cancel"),
    ]])
    await update.message.reply_text(
        f"⚠️ आपकी {count} personal memory entries redact होंगी (soft delete).\n"
        f"Aggregate anonymous summaries server-side रहेंगी।\n"
        f"This will redact {count} personal entries. Anonymous aggregates stay server-side.\n\n"
        "क्या आप पक्के हैं? / Are you sure?",
        reply_markup=keyboard,
    )


async def _execute_forgetme(message, state: dict, user_id: str) -> None:
    farmer_id = await _resolve_farmer_id(state, user_id)
    if not farmer_id:
        await message.reply_text("पहले /register करें।")
        return
    async with async_session_factory() as db:
        atoms = (
            await db.execute(
                select(MemoryAtom).where(
                    MemoryAtom.farmer_id == farmer_id,
                    MemoryAtom.redacted.is_(False),
                )
            )
        ).scalars().all()
        for atom in atoms:
            atom.redacted = True
            atom.summary = "[redacted by farmer request]"
            atom.details = {}
        await db.commit()
    await message.reply_text(
        f"✅ आपकी {len(atoms)} raw memory entries redact कर दी गईं। Anonymous aggregate summaries server-side रह सकती हैं।"
    )


EDIT_FIELDS: dict[str, tuple[str, str]] = {
    "name": ("नाम / Name", "अपना नाम भेजें / Send your name:"),
    "phone": ("फोन / Phone", "नया 10-अंक फोन नंबर भेजें / Send new 10-digit phone:"),
    "village": ("गाँव / Village", "नया गाँव भेजें / Send new village name:"),
    "tehsil": ("तहसील / Tehsil", "नई तहसील भेजें / Send new tehsil/block:"),
    "district": ("ज़िला / District", "नया ज़िला भेजें / Send new district:"),
    "preferred_language": (
        "भाषा / Language",
        "Preferred language code भेजें (hi-IN, bn-IN, kn-IN, mr-IN, ta-IN, te-IN, gu-IN, pa-IN, ml-IN, od-IN, en-IN):",
    ),
}


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /edit — let farmer revise a registered field."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    farmer_id = await _resolve_farmer_id(state, user_id)
    if not farmer_id:
        await update.message.reply_text("पहले /register करें। / Please /register first.")
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"edit_field:{key}")]
         for key, (label, _) in EDIT_FIELDS.items()]
        + [[InlineKeyboardButton("रद्द / Cancel", callback_data="edit_cancel")]]
    )
    await update.message.reply_text(
        "क्या बदलना है? / What would you like to change?",
        reply_markup=keyboard,
    )


async def _apply_edit(update, user_id: str, value: str, state: dict) -> None:
    field_key = state.get("data", {}).get("edit_field")
    if not field_key or field_key not in EDIT_FIELDS:
        state["state"] = "ready"
        return
    phone = state.get("phone", user_id)
    async with async_session_factory() as db:
        farmer = await db.scalar(select(Farmer).where(Farmer.phone == phone))
        if not farmer:
            await update.message.reply_text("⚠️ Farmer record नहीं मिला।")
            state["state"] = "ready"
            return
        clean = value.strip()
        if field_key == "phone":
            clean = clean.replace(" ", "").replace("-", "").replace("+91", "")
        setattr(farmer, field_key, clean)
        try:
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.error(f"Edit save failed: {exc}")
            await update.message.reply_text("❌ Save failed. /edit से दोबारा कोशिश करें।")
            state["state"] = "ready"
            return
    state["state"] = "ready"
    state.get("data", {}).pop("edit_field", None)
    label = EDIT_FIELDS[field_key][0]
    await update.message.reply_text(f"✅ {label} अपडेट हो गया: {clean}")


async def demo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /demo — bind a rich demo memory palace to this Telegram user."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    await update.message.reply_chat_action(ChatAction.TYPING)
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
    await update.message.reply_chat_action(ChatAction.TYPING)
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


async def _handle_profile_data(update: Update, user_id: str, text: str, state: dict):
    """Bug 2 fix: parse '<size>, <irrigation>, <water>, <soil_test>, <budget>' and persist FarmerProfile."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 5:
        await update.message.reply_text(
            "⚠️ फॉर्मेट: <आकार>, <सिंचाई>, <पानी>, <मिट्टी>, <बजट>\n"
            "Format: <size>, <irrigation>, <water>, <soil_test>, <budget>\n"
            "उदाहरण: 3 acre, borewell, seasonal, done_old, 50000"
        )
        return

    from app.models_memory import FarmerProfile

    async with async_session_factory() as db:
        phone = state.get("phone", user_id)
        farmer = await db.scalar(select(Farmer).where(Farmer.phone == phone))
        if not farmer:
            await update.message.reply_text("⚠️ पहले /register करें। Please /register first.")
            state["state"] = "start"
            return

        profile = await db.scalar(
            select(FarmerProfile).where(FarmerProfile.farmer_id == farmer.id)
        )
        if profile is None:
            profile = FarmerProfile(farmer_id=farmer.id)
            db.add(profile)

        # Parse farm size (e.g., "3 acre", "3.5", "3 एकड़")
        try:
            size_token = parts[0].split()[0]
            profile.farm_size_acres = float(size_token)
        except (ValueError, IndexError):
            profile.farm_size_acres = None

        profile.irrigation_source = parts[1].lower() or None
        profile.water_reliability = parts[2].lower() or None
        profile.soil_test_status = parts[3].lower() or None

        try:
            profile.annual_budget_rs = int(parts[4].replace(",", "").replace("₹", "").strip())
        except ValueError:
            profile.annual_budget_rs = None

        # Compute simple profile completeness
        filled = sum(
            1 for v in (
                profile.farm_size_acres,
                profile.irrigation_source,
                profile.water_reliability,
                profile.soil_test_status,
                profile.annual_budget_rs,
            ) if v not in (None, "")
        )
        profile.profile_completeness = round(filled / 5.0, 2)
        profile.last_updated = utc_now()

        await db.commit()

    state["state"] = "ready"
    await update.message.reply_text("✅ प्रोफाइल सहेज दी गई / Profile saved successfully!")


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
            sowing_date=utc_now(),
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
            rotation_text = ""
            try:
                from app.services.rotation import suggest_next_crop

                rotation = await suggest_next_crop(db, cycle.field_id)
                rotation_text = (
                    f"\n\n🔁 अगली फसल सुझाव: *{rotation['next_crop']}*"
                    f"\nविकल्प: {', '.join(rotation['alternatives'])}"
                    f"\nकारण: " + " ".join(rotation["reasoning"][:2])
                )
            except Exception as exc:
                logger.warning(f"rotation suggestion skipped: {exc}")
            await update.message.reply_text(
                f"✅ *{cycle.crop_name}* फसल समाप्त (कटाई)। /newcycle से नई फसल शुरू करें।{rotation_text}",
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


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /dashboard — open personalized farmer PWA."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    farmer_id = state.get("farmer_id")
    if not farmer_id:
        async with async_session_factory() as db:
            farmer = await db.scalar(select(Farmer).where(Farmer.phone == user_id))
            farmer_id = farmer.id if farmer else ""
    url = _dashboard_url(farmer_id=farmer_id, phone=user_id if not farmer_id else None)
    is_public = url.startswith("https://") and "localhost" not in url and "127.0.0.1" not in url
    if is_public:
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Open dashboard", url=url)]])
        body = (
            "🧭 *आपका AgriMesh Dashboard*\n\n"
            "यह पेज दिखाएगा: field weather, crop stage, NDVI, nearby clusters, मंडी, finance, memory, और पुराने सवालों का context.\n"
            "फोन में cache रहेगा ताकि कमजोर network में भी आखिरी data दिखे।"
        )
    else:
        reply_markup = None
        body = (
            "🧭 *आपका AgriMesh Dashboard* (dev mode)\n\n"
            f"Dashboard URL is local-only: `{url}`\n"
            "Open it in a browser on this machine. Telegram won't render an "
            "inline button for non-https URLs."
        )
    await update.message.reply_text(body, parse_mode="Markdown", reply_markup=reply_markup)


def _dashboard_url(farmer_id: str | None = None, phone: str | None = None) -> str:
    base = settings.dashboard_base_url.rstrip("/")
    if farmer_id:
        return f"{base}/?mode=farmer&farmer_id={farmer_id}"
    if phone:
        return f"{base}/?mode=farmer&phone={phone}"
    return f"{base}/?mode=farmer"


def _dashboard_button(
    text: str,
    farmer_id: str | None = None,
    phone: str | None = None,
) -> InlineKeyboardButton:
    """Build a dashboard button that degrades when the dashboard URL is local.

    Telegram rejects inline URL buttons whose host is localhost/127.x or
    a non-https scheme, so when running in dev with a local dashboard we
    fall back to a callback_data button (handled by `cmd_dashboard` in
    handle_callback) instead of producing a 400 BadRequest at /start.
    """
    url = _dashboard_url(farmer_id=farmer_id, phone=phone)
    is_public = url.startswith("https://") and "localhost" not in url and "127.0.0.1" not in url
    if is_public:
        return InlineKeyboardButton(text, url=url)
    return InlineKeyboardButton(text, callback_data="cmd_dashboard")


async def sources_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sources — show source freshness dashboard."""
    await update.message.reply_chat_action(ChatAction.TYPING)
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
    state = get_user_state(user_id)
    state["last_feedback"] = {"rating": rating, "comment": comment}

    # Bug 3 fix: persist to Advisory row.
    persisted = False
    advisory_id = state.get("last_advisory_id")
    if advisory_id:
        async with async_session_factory() as db:
            adv = await db.scalar(select(Advisory).where(Advisory.id == advisory_id))
            if adv is not None:
                adv.farmer_feedback = rating
                adv.feedback_text = comment or None
                await db.commit()
                persisted = True
            else:
                logger.warning(f"feedback: advisory {advisory_id} not found")

    emoji = ["", "😞", "😐", "🙂", "😊", "🌟"][rating]
    suffix = "" if persisted else " (कोई हाल की सलाह नहीं मिली / no recent advisory to attach)"
    await update.message.reply_text(f"{emoji} रेटिंग {rating}/5 दर्ज!{suffix} धन्यवाद।")


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

    # Bug 4 fix: persist outcome to the linked Observation, then record memory atom.
    observation_id = state.get("last_observation_id")
    persisted = False
    rating_map = {"improved": 5, "no_change": 3, "worsened": 1, "not_tried": 2}
    rating = rating_map.get(result, 2)

    if observation_id:
        async with async_session_factory() as db:
            obs = await db.scalar(select(Observation).where(Observation.id == observation_id))
            if obs is None:
                logger.warning(f"outcome: observation {observation_id} not found")
            else:
                age_days = (utc_now() - obs.created_at).days if obs.created_at else 0
                if age_days > 30:
                    await update.message.reply_text(
                        f"⏳ यह सलाह {age_days} दिन पुरानी है। परिणाम 30 दिनों के अंदर ही दर्ज करें।\n"
                        f"This advisory is {age_days} days old. Outcomes can only be logged within 30 days."
                    )
                    return
                obs.outcome_text = (f"{result}: {comment}" if comment else result).strip(": ")
                obs.outcome_rating = rating
                obs.outcome_logged_at = utc_now()
                await db.commit()
                persisted = True

                # Sprint 1: record outcome atom (M2 will boost causal-chain confidence)
                try:
                    from app.services.memory import extract_from_outcome
                    await extract_from_outcome(db, observation_id, result, comment, rating)
                except Exception as exc:  # never break user flow on memory failure
                    logger.error(f"extract_from_outcome failed: {exc}")

                # Outbreak warning: negative outcomes on disease/pest reports
                # may trip the 10% threshold for nearby farmers.
                if result in ("worsened", "no_change"):
                    try:
                        await _maybe_fire_outbreak_warning(
                            db, context, obs, comment=comment
                        )
                    except Exception as exc:
                        logger.error(f"outbreak check failed: {exc}")

    suffix = "" if persisted else " (कोई हाल का अवलोकन नहीं / no recent observation linked)"
    await update.message.reply_text(f"📝 परिणाम '{result}' दर्ज!{suffix} सीखने के लिए धन्यवाद। 🌱")


async def _maybe_fire_outbreak_warning(
    db,
    context: ContextTypes.DEFAULT_TYPE,
    observation: Observation,
    *,
    comment: str = "",
) -> None:
    """
    Run the outbreak threshold check after a negative /outcome and, if it
    trips, push the warning to every matching farmer in scope.
    """
    from app.services.outbreak import (
        check_and_trigger_outbreak,
        format_warning_message,
        list_target_farmers,
        mark_outbreak_notified,
        write_outbreak_memory_atoms,
    )

    cycle = await db.scalar(
        select(CropCycle).where(CropCycle.id == observation.crop_cycle_id)
    )
    if cycle is None:
        return

    threat_label = _extract_threat_label(observation, comment)
    if not threat_label:
        return

    alert = await check_and_trigger_outbreak(
        db,
        reporter_farmer_id=observation.farmer_id,
        crop_name=cycle.crop_name,
        pest_or_disease=threat_label,
    )
    if alert is None:
        return

    targets = await list_target_farmers(db, alert)
    if not targets:
        return

    warning = format_warning_message(alert)
    sent_ids: list[str] = []
    for farmer in targets:
        if not farmer.phone:
            continue
        try:
            await context.bot.send_message(chat_id=farmer.phone, text=warning)
            sent_ids.append(farmer.id)
        except Exception as exc:
            logger.warning(f"outbreak push failed for {farmer.id}: {exc}")

    if sent_ids:
        await mark_outbreak_notified(db, alert, sent_ids)
        await write_outbreak_memory_atoms(
            db, alert, [f for f in targets if f.id in set(sent_ids)]
        )


def _extract_threat_label(observation: Observation, comment: str) -> str:
    """Best-effort pest/disease label from vision analysis or free text."""
    vision = observation.vision_analysis or {}
    for key in ("suspected_disease", "disease", "pest", "issue"):
        val = vision.get(key) if isinstance(vision, dict) else None
        if val:
            return str(val)
    pool = " ".join(
        s for s in (observation.text_content, observation.outcome_text, comment) if s
    ).lower()
    keywords = [
        "blast", "blight", "rust", "smut", "wilt", "rot", "mildew",
        "leaf curl", "yellow vein", "stem borer", "leaf folder",
        "brown plant hopper", "aphid", "thrips", "whitefly", "mite",
        "bollworm", "fall armyworm", "shoot borer",
        "तना छेदक", "झुलसा", "फफूंद", "रतुआ",
    ]
    for kw in keywords:
        if kw in pool:
            return kw
    return ""


# ─── Conversation thread commands ─────────────────────────────────────


async def _resolve_farmer_id(state: dict, user_id: str) -> str | None:
    farmer_id = state.get("farmer_id")
    if farmer_id:
        return farmer_id
    async with async_session_factory() as db:
        farmer = await db.scalar(select(Farmer).where(Farmer.phone == user_id))
        if farmer:
            state["farmer_id"] = farmer.id
            return farmer.id
    return None


async def _current_active_thread(db, farmer_id: str, state: dict) -> ConversationThread | None:
    field_id = state.get("field_id")
    crop_cycle_id = state.get("crop_cycle_id")
    if not field_id:
        field = await db.scalar(
            select(Field).where(Field.farmer_id == farmer_id).order_by(desc(Field.created_at)).limit(1)
        )
        field_id = field.id if field else None
    if field_id and not crop_cycle_id:
        cycle = await db.scalar(
            select(CropCycle)
            .where(CropCycle.field_id == field_id, CropCycle.is_active == True)  # noqa: E712
            .order_by(desc(CropCycle.created_at))
            .limit(1)
        )
        crop_cycle_id = cycle.id if cycle else None
    return await db.scalar(
        select(ConversationThread).where(
            ConversationThread.farmer_id == farmer_id,
            ConversationThread.field_id == field_id,
            ConversationThread.crop_cycle_id == crop_cycle_id,
            ConversationThread.channel == "telegram",
            ConversationThread.is_active == True,  # noqa: E712
        )
    )


async def _archive_active_thread(user_id: str, state: dict) -> bool:
    farmer_id = await _resolve_farmer_id(state, user_id)
    if not farmer_id:
        return False
    async with async_session_factory() as db:
        thread = await _current_active_thread(db, farmer_id, state)
        if not thread:
            return False
        thread.is_active = False
        await db.commit()
    return True


async def _show_threads(update: Update, user_id: str, show_archived: bool = False):
    """Render the threads list. Works for both /threads command and inline callback."""
    state = get_user_state(user_id)
    callback_msg = update.callback_query.message if update.callback_query else None

    async def _send(text: str, reply_markup=None):
        if callback_msg is not None:
            try:
                await callback_msg.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
            except BadRequest:
                await callback_msg.edit_text(text.replace("*", ""), reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    farmer_id = await _resolve_farmer_id(state, user_id)
    if not farmer_id:
        await _send("❌ पहले रजिस्टर करें / Please register first using /start.")
        return

    async with async_session_factory() as db:
        query = (
            select(ConversationThread)
            .where(
                ConversationThread.farmer_id == farmer_id,
                ConversationThread.channel == "telegram",
                ConversationThread.is_active == (not show_archived),
            )
            .order_by(desc(ConversationThread.updated_at))
            .limit(5 if show_archived else 8)
        )
        threads = (await db.execute(query)).scalars().all()

        if not threads:
            text = (
                "📁 कोई पुरानी बातचीत नहीं / No archived conversations."
                if show_archived
                else "अभी कोई बातचीत नहीं। कोई सवाल पूछें / No conversations yet. Ask a question."
            )
            buttons = (
                [[InlineKeyboardButton("⬅️ मुख्य / Active", callback_data="threads_list")]]
                if show_archived
                else []
            )
            await _send(text, InlineKeyboardMarkup(buttons) if buttons else None)
            return

        lines = ["🗣️ *आपकी बातचीतें / Your Conversations*", ""]
        buttons: list[list[InlineKeyboardButton]] = []
        for thread in threads:
            field = await db.get(Field, thread.field_id) if thread.field_id else None
            cycle = await db.get(CropCycle, thread.crop_cycle_id) if thread.crop_cycle_id else None
            crop_label = cycle.crop_name if cycle else "general"
            field_label = field.name if field else "—"
            label = thread.title or f"{crop_label} • {field_label}"
            if show_archived:
                label = "📁 " + label
            label = f"{label} ({thread.turn_count})"
            buttons.append([InlineKeyboardButton(label[:60], callback_data=f"thread_switch:{thread.id}")])

        if show_archived:
            buttons.append([InlineKeyboardButton("⬅️ मुख्य / Active", callback_data="threads_list")])
        else:
            buttons.append([InlineKeyboardButton("पुराने / Show archived", callback_data="threads_show_archived")])

        await _send("\n".join(lines), InlineKeyboardMarkup(buttons))


async def threads_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /threads — list active conversation threads."""
    user_id = str(update.effective_user.id)
    await _show_threads(update, user_id, show_archived=False)


async def newthread_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /newthread — archive current thread and start fresh (with confirm)."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    farmer_id = await _resolve_farmer_id(state, user_id)
    if not farmer_id:
        await update.message.reply_text("❌ पहले रजिस्टर करें / Please register first using /start.")
        return
    async with async_session_factory() as db:
        active = await _current_active_thread(db, farmer_id, state)
    if not active:
        await update.message.reply_text(
            "अभी कोई सक्रिय बातचीत नहीं। पहले सवाल पूछें / No active conversation yet. Ask a question first."
        )
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("हाँ / Yes, close it", callback_data="newthread_confirm")],
        [InlineKeyboardButton("नहीं / Cancel", callback_data="newthread_cancel")],
    ])
    await update.message.reply_text(
        "इस बातचीत को बंद करें और नई शुरू करें? / Close this conversation and start fresh?",
        reply_markup=keyboard,
    )


async def endthread_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /endthread — archive the current active thread."""
    user_id = str(update.effective_user.id)
    state = get_user_state(user_id)
    if await _archive_active_thread(user_id, state):
        await update.message.reply_text("बातचीत बंद कर दी गई / Conversation closed.")
    else:
        await update.message.reply_text(
            "अभी कोई सक्रिय बातचीत नहीं / No active conversation."
        )


# ─── Bot Runner ───────────────────────────────────────────────────────

BOT_COMMAND_MENU: list[tuple[str, str]] = [
    ("start", "Main menu, resume chat"),
    ("demo", "Load sample farm + memory"),
    ("register", "Register (text step-by-step)"),
    ("edit", "Edit registration details"),
    ("dashboard", "Open private dashboard"),
    ("prices", "Mandi prices + MSP"),
    ("tasks", "Today's tasks"),
    ("calendar", "Crop calendar"),
    ("memory", "Field memories and patterns"),
    ("threads", "List conversations"),
    ("newthread", "Start a new conversation"),
    ("endthread", "Close current conversation"),
    ("newcycle", "Start a new crop cycle"),
    ("closecycle", "Close cycle + rotation tip"),
    ("expense", "Log an expense"),
    ("sale", "Log a sale"),
    ("finance", "Profit & loss summary"),
    ("voice_lang", "Set voice language (hi-IN…)"),
    ("voice_reply", "Reply mode: text/both/voice"),
    ("mydata", "Show my saved memory"),
    ("forgetme", "Redact my raw memory"),
    ("why", "Explain last advice"),
    ("sources", "Show evidence sources"),
    ("help", "All commands"),
]


async def _post_init(app: Application) -> None:
    """Set the BotFather command menu so '/' inside Telegram shows commands."""
    try:
        await app.bot.set_my_commands([BotCommand(c, d) for c, d in BOT_COMMAND_MENU])
        logger.info(f"Telegram command menu registered ({len(BOT_COMMAND_MENU)} commands).")
    except Exception as exc:
        logger.warning(f"set_my_commands failed: {exc}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help — show all commands."""
    await update.message.reply_text(_help_text(), parse_mode="Markdown")


def create_bot() -> Application:
    """Create and configure the Telegram bot application."""
    token = settings.telegram_bot_token.strip()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set! Bot will not start.")
        return None

    app = Application.builder().token(token).post_init(_post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
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
    app.add_handler(CommandHandler("mydata", mydata_command))
    app.add_handler(CommandHandler("forgetme", forgetme_command))
    app.add_handler(CommandHandler("edit", edit_command))
    app.add_handler(CommandHandler("dashboard", dashboard_command))
    app.add_handler(CommandHandler("why", why_command))
    app.add_handler(CommandHandler("sources", sources_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("outcome", outcome_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("threads", threads_command))
    app.add_handler(CommandHandler("newthread", newthread_command))
    app.add_handler(CommandHandler("endthread", endthread_command))
    app.add_handler(CommandHandler("voice_lang", cmd_voice_lang))
    app.add_handler(CommandHandler("voice_reply", cmd_voice_reply))

    # Messages (structured data capture runs first)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

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
