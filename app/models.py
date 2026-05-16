"""
AgriMesh V4.0 — SQLAlchemy ORM Models (~18 tables)
Crop-agnostic, region-agnostic. V4.0 scope cut from V3.0's 29 tables.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.utils.time import utc_now

# ─── Enums ────────────────────────────────────────────────────────────

class RiskLevel:
    NORMAL = "NORMAL"
    WATCH = "WATCH"
    PREVENTIVE_ACTION = "PREVENTIVE_ACTION"
    ESCALATE = "ESCALATE"


class Confidence:
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CropStage:
    PRE_SOWING = "pre_sowing"
    SEEDLING = "seedling"
    VEGETATIVE = "vegetative"
    FLOWERING = "flowering"
    FRUITING = "fruiting"
    HARVEST = "harvest"
    POST_HARVEST = "post_harvest"


class AlertStatus:
    PENDING = "pending"
    REVIEWED = "reviewed"
    BROADCAST = "broadcast"
    DISMISSED = "dismissed"


class AlertKind:
    PATTERN = "pattern"      # nightly co-occurrence cluster (legacy default)
    OUTBREAK = "outbreak"    # threshold-tripped pest/disease warning


# ─── Auth ─────────────────────────────────────────────────────────────

class Farmer(Base):
    __tablename__ = "farmers"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    phone = Column(String(15), unique=True, nullable=False, index=True)
    hashed_password = Column(String(128), nullable=False)
    name = Column(String(120), nullable=False)
    preferred_language = Column(String(10), default="hi")  # hi, en
    district = Column(String(80), index=True)
    tehsil = Column(String(80))
    village = Column(String(120))
    registration_date = Column(DateTime, default=utc_now)
    is_active = Column(Boolean, default=True)

    # relationships
    fields = relationship(
        "Field",
        back_populates="farmer",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    observations = relationship(
        "Observation",
        back_populates="farmer",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class ExtensionWorker(Base):
    __tablename__ = "extension_workers"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    phone = Column(String(15), unique=True, nullable=False, index=True)
    hashed_password = Column(String(128), nullable=False)
    name = Column(String(120), nullable=False)
    district = Column(String(80), index=True)
    tehsil = Column(String(80))
    assigned_villages = Column(JSON, default=list)  # ["village_a", "village_b"]
    is_active = Column(Boolean, default=True)


# ─── Farm & Field ─────────────────────────────────────────────────────

class Field(Base):
    __tablename__ = "fields"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    farmer_id = Column(String(36), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(80))
    area_acres = Column(Float)
    soil_type = Column(String(60))  # clay_loam, sandy_loam, black_cotton, etc.
    soil_ph = Column(Float, nullable=True)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    irrigation_type = Column(String(40))  # canal, borewell, rainfed, drip
    created_at = Column(DateTime, default=utc_now)

    farmer = relationship("Farmer", back_populates="fields")
    crop_cycles = relationship(
        "CropCycle",
        back_populates="field",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class CropCycle(Base):
    """One crop season on one field."""
    __tablename__ = "crop_cycles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    field_id = Column(String(36), ForeignKey("fields.id", ondelete="CASCADE"), nullable=False, index=True)
    crop_name = Column(String(60), nullable=False)  # rice, wheat, maize, etc.
    variety = Column(String(60), nullable=True)
    sowing_date = Column(DateTime)
    expected_harvest_date = Column(DateTime, nullable=True)
    current_stage = Column(String(30), default=CropStage.PRE_SOWING)
    is_active = Column(Boolean, default=True)
    is_template = Column(Boolean, default=False)  # true → reusable crop calendar template
    created_at = Column(DateTime, default=utc_now)

    field = relationship("Field", back_populates="crop_cycles")
    tasks = relationship(
        "CropCalendarTask",
        back_populates="cycle",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    observations = relationship(
        "Observation",
        back_populates="crop_cycle",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class CropCalendarTask(Base):
    """Tasks within a crop cycle (merged template + instance from V3.0)."""
    __tablename__ = "crop_calendar_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cycle_id = Column(String(36), ForeignKey("crop_cycles.id", ondelete="CASCADE"), nullable=False, index=True)
    stage = Column(String(30), nullable=False)
    task_name = Column(String(120), nullable=False)
    description = Column(Text)
    days_from_sowing = Column(Integer, nullable=True)
    completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)

    cycle = relationship("CropCycle", back_populates="tasks")


# ─── Observations & Outcomes (merged from V3.0's separate tables) ────

class Observation(Base):
    """Farmer observations: photos, text, voice. Outcomes merged in (outcome_logged_at)."""
    __tablename__ = "observations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    farmer_id = Column(String(36), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True)
    crop_cycle_id = Column(String(36), ForeignKey("crop_cycles.id", ondelete="CASCADE"), nullable=False, index=True)
    field_id = Column(String(36), ForeignKey("fields.id", ondelete="SET NULL"), nullable=True)

    observation_type = Column(String(30))  # photo, text, voice
    text_content = Column(Text, nullable=True)  # transcribed or typed
    image_path = Column(String(300), nullable=True)
    audio_path = Column(String(300), nullable=True)

    # Vision analysis results
    vision_analysis = Column(JSON, nullable=True)
    vision_confidence = Column(Float, nullable=True)

    # Stage at observation time
    reported_stage = Column(String(30), nullable=True)

    # Outcome fields (nullable — only filled when observation has an outcome)
    outcome_text = Column(Text, nullable=True)
    outcome_rating = Column(Integer, nullable=True)  # 1–5 farmer feedback
    outcome_logged_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=utc_now)

    farmer = relationship("Farmer", back_populates="observations")
    crop_cycle = relationship("CropCycle", back_populates="observations")
    advisory = relationship(
        "Advisory",
        back_populates="observation",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",
    )


# ─── Advisory & Verifier ──────────────────────────────────────────────

class Advisory(Base):
    """Every recommendation the agent produces. One per observation."""
    __tablename__ = "advisories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    observation_id = Column(String(36), ForeignKey("observations.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    farmer_id = Column(String(36), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True)

    # Agent output
    risk_level = Column(String(30))  # RiskLevel enum
    confidence = Column(String(10))  # Confidence enum
    selected_action_indices = Column(JSON)  # [1, 3, 5] — indices into wiki actions
    selected_warning_indices = Column(JSON)  # [2] — indices into wiki warnings
    actions_text = Column(JSON)  # resolved text of selected actions
    warnings_text = Column(JSON)
    contextualization = Column(Text)

    # How it was generated
    thinking_enabled = Column(Boolean, default=False)
    model_used = Column(String(30))
    retrieval_path = Column(String(10))  # fast, graph
    latency_ms = Column(Integer)

    # Evidence bundle (what was retrieved)
    evidence_article_ids = Column(JSON)
    weather_data = Column(JSON, nullable=True)
    mandi_data = Column(JSON, nullable=True)
    scheme_data = Column(JSON, nullable=True)

    # Memory reference
    memory_reference = Column(Text, nullable=True)
    previous_observation_id = Column(String(36), nullable=True)

    # Audit
    created_at = Column(DateTime, default=utc_now)
    farmer_feedback = Column(Integer, nullable=True)
    feedback_text = Column(Text, nullable=True)

    observation = relationship("Observation", back_populates="advisory")
    verifier_report = relationship(
        "VerifierReport",
        back_populates="advisory",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class VerifierReport(Base):
    """Auditable verifier pass/fail report for every advisory."""
    __tablename__ = "verifier_reports"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    advisory_id = Column(String(36), ForeignKey("advisories.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    passes_all = Column(Boolean, default=False)

    # Structural checks
    actions_exist_in_wiki = Column(Boolean, default=False)
    warnings_exist_in_wiki = Column(Boolean, default=False)
    indices_in_range = Column(Boolean, default=False)

    # Semantic checks
    actions_match_risk_type = Column(Boolean, default=False)
    actions_dont_contradict_memory = Column(Boolean, default=False)

    # Safety checks
    passes_regex_filter = Column(Boolean, default=False)
    passes_llm_safety_check = Column(Boolean, default=False)

    # Calibration
    confidence_calibrated_to_evidence = Column(Boolean, default=False)

    details = Column(JSON, default=dict)
    created_at = Column(DateTime, default=utc_now)

    advisory = relationship("Advisory", back_populates="verifier_report")


# ─── Wiki Graph ───────────────────────────────────────────────────────

class WikiArticle(Base):
    """Crop-agnostic wiki articles with graph edges for multi-hop retrieval."""
    __tablename__ = "wiki_articles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(200), nullable=False)
    title_hi = Column(String(200), nullable=True)
    content = Column(Text, nullable=False)
    content_hi = Column(Text, nullable=True)
    summary = Column(Text)  # 2–3 sentence summary for retrieval
    summary_hi = Column(Text, nullable=True)

    # Tag-based metadata (crop-agnostic)
    applicable_crops = Column(JSON, default=list)  # ["rice", "wheat"] or [] = all
    applicable_stages = Column(JSON, default=list)  # ["seedling", "vegetative"]
    topic_tags = Column(JSON, default=list)  # ["fungal_disease", "nutrient_deficiency"]
    risk_level = Column(String(30), default=RiskLevel.WATCH)  # typical risk if untreated

    # Graph edges (§5.5 — all 8 relationship types)
    causes_of = Column(JSON, default=list)          # → this causes [article IDs]
    prevented_by = Column(JSON, default=list)       # → prevented by [article IDs]
    correlated_with = Column(JSON, default=list)    # → correlated with [article IDs]
    followed_by = Column(JSON, default=list)        # → often followed by [article IDs]
    treated_by = Column(JSON, default=list)         # → treated by [article IDs]
    aggravated_by = Column(JSON, default=list)      # → aggravated by [article IDs]
    variant_of = Column(JSON, default=list)         # → is a variant of [article IDs]
    regional_of = Column(JSON, default=list)        # → regional variant of [article IDs]
    confused_with = Column(JSON, default=list)      # → easily confused with [article IDs]

    # Actions & warnings (indexed for template selection)
    actions = Column(JSON, default=list)  # ["Apply X at Y rate", "Drain field for Z hours"]
    warnings = Column(JSON, default=list)  # ["Do NOT apply during flowering", "Avoid if rain expected"]

    # Metadata
    source_url = Column(String(300), nullable=True)
    last_reviewed = Column(DateTime, default=utc_now)
    review_status = Column(String(20), default="published")  # draft, published, deprecated
    confidence_score = Column(Float, default=0.85)


# ─── Cluster / Alert ─────────────────────────────────────────────────

class AlertCluster(Base):
    """Grouped alerts for extension worker review. V3.0's alert_cluster_observations merged via JSON array."""
    __tablename__ = "alert_clusters"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    district = Column(String(80), index=True)
    tehsil = Column(String(80), index=True)
    village = Column(String(120), nullable=True)
    crop_name = Column(String(60), index=True)
    issue_category = Column(String(60))  # fungal_disease, pest, nutrient, weather_damage

    observation_ids = Column(JSON, default=list)  # list of observation IDs in this cluster
    advisory_ids = Column(JSON, default=list)  # associated advisory IDs
    farmer_count = Column(Integer, default=0)
    severity = Column(Float, default=0.0)  # aggregated severity score
    status = Column(String(20), default=AlertStatus.PENDING)
    reviewed_by = Column(String(36), ForeignKey("extension_workers.id", ondelete="SET NULL"), nullable=True)
    broadcast_message = Column(Text, nullable=True)
    broadcast_at = Column(DateTime, nullable=True)
    farmers_notified = Column(Integer, default=0)
    created_at = Column(DateTime, default=utc_now)

    # Outbreak warning extension (10%-threshold pest/disease alerts)
    kind = Column(String(20), default=AlertKind.PATTERN, index=True)
    scope = Column(String(20), nullable=True)         # village | tehsil | district
    pest_or_disease = Column(String(120), nullable=True)
    reporting_farmer_ids = Column(JSON, default=list)  # farmers whose reports tripped threshold
    notified_farmer_ids = Column(JSON, default=list)   # farmers who received the warning
    consumed_farmer_ids = Column(JSON, default=list)   # farmers for whom the prepend already fired
    expires_at = Column(DateTime, nullable=True, index=True)

    extension_worker = relationship("ExtensionWorker")


# ─── Finance ──────────────────────────────────────────────────────────

class FinanceEntry(Base):
    __tablename__ = "finance_entries"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    farmer_id = Column(String(36), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True)
    crop_cycle_id = Column(String(36), ForeignKey("crop_cycles.id", ondelete="SET NULL"), nullable=True)
    entry_type = Column(String(20))  # expense, revenue, loan
    category = Column(String(40))  # seed, fertilizer, pesticide, labour, irrigation, harvest_sale
    amount = Column(Float, nullable=False)
    description = Column(Text)
    recorded_at = Column(DateTime, default=utc_now)


# ─── NDVI / Satellite Seed Data ───────────────────────────────────────

class SatelliteNDVI(Base):
    """Seeded NDVI data with real API shape (for future Sentinel-2 swap)."""
    __tablename__ = "satellite_ndvi"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    field_id = Column(String(36), ForeignKey("fields.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(DateTime, nullable=False)
    ndvi_value = Column(Float, nullable=False)
    source = Column(String(30), default="seeded")  # seeded, sentinel2
    cloud_cover_pct = Column(Float, nullable=True)


# ─── Indexes ──────────────────────────────────────────────────────────

Index("ix_observations_farmer_cycle", Observation.farmer_id, Observation.crop_cycle_id)
Index("ix_observations_field_created", Observation.field_id, Observation.created_at)
Index("ix_observations_created_at", Observation.created_at)
Index("ix_advisories_farmer_created", Advisory.farmer_id, Advisory.created_at)
Index("ix_crop_cycles_active_field_created", CropCycle.field_id, CropCycle.is_active, CropCycle.created_at)
Index("ix_satellite_ndvi_field_date", SatelliteNDVI.field_id, SatelliteNDVI.date)
Index("ix_clusters_district_crop", AlertCluster.district, AlertCluster.crop_name)
Index("ix_clusters_status", AlertCluster.status)
Index("ix_alert_clusters_status_severity", AlertCluster.status, AlertCluster.severity)
Index("ix_alert_clusters_district_severity", AlertCluster.district, AlertCluster.severity)
Index("ix_alert_clusters_kind_crop_village", AlertCluster.kind, AlertCluster.crop_name, AlertCluster.village)
Index("ix_alert_clusters_kind_expires", AlertCluster.kind, AlertCluster.expires_at)
Index("ix_finance_farmer_cycle", FinanceEntry.farmer_id, FinanceEntry.crop_cycle_id)
Index("ix_finance_farmer_cycle_type", FinanceEntry.farmer_id, FinanceEntry.crop_cycle_id, FinanceEntry.entry_type)
Index("ix_finance_entries_recorded_at", FinanceEntry.recorded_at)
