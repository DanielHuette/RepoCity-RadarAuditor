"""Typed rows of the CRM tables the worker reads (schema crm of the same Supabase project).

Context: The CRM (repocity-crm) owns these tables; the worker reads them through PostgREST with the
service key and changes mail triage rows only through the CRM's functions. Only the columns the
worker uses are modelled; extra columns are ignored.
Input: Rows of crm.customers, crm.quotes, crm.quote_items, crm.orders, crm.products, the
claimed rows of crm.mail_triage and crm.assistant_requests, and the answer of
crm.apply_assistant_intent.
Output: Validated pydantic models.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _CrmRow(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CrmCustomer(_CrmRow):
    """The addressee of a quote as the CRM stores it."""

    id: UUID
    name: str
    contact_person: str | None = None
    email: str | None = None
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None


class CrmQuote(_CrmRow):
    """A quote with the totals the CRM computed; status draft, sent, accepted, declined, expired."""

    id: UUID
    number: str
    customer_id: UUID
    status: str
    issue_date: date | None = None
    valid_until: date | None = None
    intro: str | None = None
    outro: str | None = None
    net: Decimal
    vat: Decimal
    gross: Decimal


class CrmQuoteItem(_CrmRow):
    """One position of a quote."""

    id: UUID
    quote_id: UUID
    position: int
    product_id: UUID | None = None
    title: str
    description: str | None = None
    quantity: Decimal
    unit: str | None = None
    unit_price_net: Decimal
    vat_rate: Decimal


class CrmOrder(_CrmRow):
    """An order the CRM created when a quote was accepted."""

    id: UUID
    number: str
    customer_id: UUID
    quote_id: UUID | None = None
    title: str
    status: str


class CrmProduct(_CrmRow):
    """A product of the price list; radar_areas are the catalogue areas it improves."""

    id: UUID
    name: str
    description: str | None = None
    radar_areas: list[str] = Field(default_factory=list)


class MailTriageKind(StrEnum):
    """What the CRM wants to know about a mail."""

    UNKNOWN_SENDER = "unknown_sender"
    CUSTOMER_MAIL = "customer_mail"


class MailTriage(_CrmRow):
    """A mail the CRM queued for Claude Code; subject and body are untrusted text."""

    id: UUID
    kind: MailTriageKind
    direction: str | None = None
    address: str | None = None
    name: str | None = None
    subject: str | None = None
    body_text: str | None = None
    attempts: int = 0


class AssistantRequest(_CrmRow):
    """A request Daniel typed or spoke in the Cockpit; its text is untrusted until read as intent.

    A spoken request has audio_path and no text until its transcript is saved.
    """

    id: UUID
    text: str | None = None
    audio_path: str | None = None
    attempts: int = 0


class AssistantResult(_CrmRow):
    """Answer of crm.apply_assistant_intent: whether it was done, and the German sentence."""

    ok: bool
    text: str
