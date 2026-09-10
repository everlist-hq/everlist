"""Data models for the agenthub SDK (SPEC v2 shapes)."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Listing:
    id: str
    vertical: str
    title: str
    price: float
    capacity: int
    registered: int = 0
    available: bool = True
    extra: Optional[dict] = None

    @classmethod
    def from_api(cls, d: dict) -> "Listing":
        known = {"id", "vertical", "title", "price", "capacity", "registered", "available"}
        return cls(
            id=d["id"], vertical=d.get("vertical", ""), title=d.get("title", ""),
            price=float(d.get("price", 0)), capacity=int(d.get("capacity", 0)),
            registered=int(d.get("registered", 0)),
            available=bool(d.get("available", True)),
            extra={k: v for k, v in d.items() if k not in known})


@dataclass
class Booking:
    id: str
    listing_id: str
    vertical: str
    escrow: str            # HELD | RELEASED | REFUNDED
    amount: float
    hub_fee: float
    owner_payout: float
    quantity: int
    booked_by: str
    secret: Optional[str] = None      # returned ONLY to the booking principal
    cancel_token: Optional[str] = None
    replayed: bool = False
    extra: Optional[dict] = None

    @classmethod
    def from_api(cls, d: dict) -> "Booking":
        known = {"id", "listing_id", "vertical", "escrow", "amount", "hub_fee",
                 "owner_payout", "quantity", "booked_by", "booking_secret", "cancel_token", "replayed"}
        return cls(
            id=d["id"], listing_id=d.get("listing_id", ""), vertical=d.get("vertical", ""),
            escrow=d.get("escrow", ""), amount=float(d.get("amount", 0)),
            hub_fee=float(d.get("hub_fee", 0)), owner_payout=float(d.get("owner_payout", 0)),
            quantity=int(d.get("quantity", 1)), booked_by=d.get("booked_by", ""),
            secret=d.get("booking_secret"), cancel_token=d.get("cancel_token"),
            replayed=bool(d.get("replayed", False)),
            extra={k: v for k, v in d.items() if k not in known})
