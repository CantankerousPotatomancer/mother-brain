from __future__ import annotations
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field
from typing import Literal


# --- Entity types ---

EntityType = Literal[
    "self", "project", "person", "system", "organization", "concept", "reference"
]
FactCategory = Literal[
    "status", "decision", "preference", "technical",
    "personal", "relationship", "financial", "goal", "other",
]
FactSource = Literal["conversation", "user_stated", "inferred", "system"]
ObligationStatus = Literal["active", "completed", "deferred", "dropped"]
GoalHorizon = Literal["immediate", "short", "medium", "long", "life"]
GoalStatus = Literal["active", "achieved", "abandoned", "deferred"]
EventRecurrence = Literal["none", "daily", "weekly", "monthly", "yearly"]
EventCategory = Literal[
    "deadline", "birthday", "anniversary", "appointment", "release", "reminder", "other"
]
ReferenceCategory = Literal["book", "article", "tool", "link", "video", "other"]
ReferenceStatus = Literal["unread", "reading", "done", "archived"]


# --- DB row models ---

class Entity(BaseModel):
    id: UUID
    name: str
    type: EntityType
    aliases: list[str] = []
    touches: int = 0
    last_touched: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Fact(BaseModel):
    id: UUID
    entity_id: UUID
    content: str
    category: FactCategory
    confidence: float = 1.0
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    source: FactSource = "conversation"
    touches: int = 0
    last_touched: datetime | None = None
    created_at: datetime | None = None
    entity_name: str | None = None  # joined from entities


class Summary(BaseModel):
    id: UUID
    entity_id: UUID
    content: str
    fact_count: int = 0
    touches: int = 0
    last_touched: datetime | None = None
    last_updated: datetime | None = None
    entity_name: str | None = None
    entity_type: EntityType | None = None


class Episode(BaseModel):
    id: UUID
    title: str
    summary: str
    entity_ids: list[UUID] = []
    touches: int = 0
    last_touched: datetime | None = None
    occurred_at: datetime | None = None


class WorkingMemoryEntry(BaseModel):
    id: UUID
    entity_id: UUID
    reason: str | None = None
    touches: int = 0
    last_touched: datetime | None = None
    activated_at: datetime | None = None
    expires_at: datetime | None = None
    entity_name: str | None = None
    entity_type: EntityType | None = None


class Event(BaseModel):
    id: UUID
    title: str
    description: str | None = None
    entity_ids: list[UUID] = []
    event_date: datetime
    recurrence: EventRecurrence = "none"
    category: EventCategory = "other"
    touches: int = 0
    last_touched: datetime | None = None
    created_at: datetime | None = None


class Obligation(BaseModel):
    id: UUID
    title: str
    description: str | None = None
    entity_ids: list[UUID] = []
    status: ObligationStatus = "active"
    priority: int = 2
    due_date: datetime | None = None
    completed_at: datetime | None = None
    touches: int = 0
    last_touched: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Goal(BaseModel):
    id: UUID
    title: str
    description: str | None = None
    entity_ids: list[UUID] = []
    horizon: GoalHorizon
    status: GoalStatus = "active"
    parent_id: UUID | None = None
    touches: int = 0
    last_touched: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Relationship(BaseModel):
    id: UUID
    entity_id: UUID
    relationship: str
    context: str | None = None
    shared_projects: list[UUID] = []
    cadence: str | None = None
    notes: str | None = None
    touches: int = 0
    last_touched: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    entity_name: str | None = None


class Reference(BaseModel):
    id: UUID
    title: str
    url: str | None = None
    description: str | None = None
    category: ReferenceCategory = "other"
    entity_ids: list[UUID] = []
    status: ReferenceStatus = "unread"
    touches: int = 0
    last_touched: datetime | None = None
    created_at: datetime | None = None


# --- Composite result models ---

class RecallResultItem(BaseModel):
    summary: Summary
    entity: Entity
    active_obligation_count: int = 0
    active_event_count: int = 0
    similarity: float = 0.0


class RecallResult(BaseModel):
    working_memory: list[WorkingMemoryEntry] = []
    results: list[RecallResultItem] = []


class UpcomingResult(BaseModel):
    events: list[Event] = []
    obligations: list[Obligation] = []


# --- Dedup result ---

class DedupResult(BaseModel):
    action: Literal["insert", "replace", "flag"]
    existing_id: UUID | None = None
    score: float = 0.0
