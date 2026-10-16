"""What each tagging agent must return. Enforced by structured outputs, then checked by validate.py."""

from typing import Literal

from pydantic import BaseModel, Field


class UnitProposal(BaseModel):
    id: str = Field(description="Short equipment ID, e.g. AHU-1, VAV-S, CH-2, CT-1, HW-LOOP")
    type: str = Field(description="One of the allowed equipment types")
    served_by: str | None = Field(description="ID of the unit that feeds or hosts this one (AHU for a VAV, plant/loop for a boiler, chiller, tower, or pump), or null")
    points: list[str] = Field(description="Every raw point name that belongs to this unit")
    rationale: str = Field(description="One sentence: why these points form this unit")


class EquipmentProposal(BaseModel):
    units: list[UnitProposal]
    notes: str = Field(description="Anything uncertain a reviewer should look at; empty if none")


class PointDecision(BaseModel):
    point: str
    action: Literal["tag", "exclude"]
    tags: list[str] = Field(description="Haystack tags if action is tag; empty if exclude")
    exclude_reason: str = Field(description="Why the point isn't worth keeping if action is exclude; empty if tag")
    confidence: Literal["high", "medium", "low"]


class UnitTagging(BaseModel):
    decisions: list[PointDecision]


class SingleAgentOutput(BaseModel):
    """The single-agent baseline does both stages in one response."""
    units: list[UnitProposal]
    decisions: list[PointDecision]
    notes: str
