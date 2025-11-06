"""
Database Schemas

Pydantic models that define MongoDB collections for the online voting system.
Each class name maps to a collection with the lowercase name.

Guiding principles:
- Enforce one-vote-per-NIM using a separate voter status collection
- Keep votes anonymous: no NIM is ever stored with a vote record
"""

from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List

class Voter(BaseModel):
    """
    Eligible voters list
    Collection: "voter"
    """
    nim: str = Field(..., min_length=8, max_length=20, description="Student ID (NIM)")
    name: Optional[str] = Field(None, description="Full name of the voter")
    program: Optional[str] = Field(None, description="Program/Department")
    eligible: bool = Field(True, description="Whether this NIM is eligible to vote")

class VoterStatus(BaseModel):
    """
    Voting status per NIM (separate from Voter to avoid linking to choices)
    Collection: "voterstatus"
    """
    nim: str = Field(..., min_length=8, max_length=20)
    has_voted: bool = Field(False, description="Whether the voter has already voted")
    voted_at: Optional[str] = Field(None, description="ISO timestamp when the vote was cast")

class Candidate(BaseModel):
    """
    Candidate pair information
    Collection: "candidate"
    """
    number: int = Field(..., ge=1, description="Ballot number")
    president_name: str = Field(..., description="Presidential candidate name")
    vice_name: str = Field(..., description="Vice presidential candidate name")
    vision: Optional[str] = Field(None, description="Vision statement")
    mission: Optional[List[str]] = Field(default=None, description="List of mission points")
    photo_url: Optional[HttpUrl] = Field(None, description="Photo URL for the candidate pair")

class Vote(BaseModel):
    """
    Anonymous vote record (no NIM stored)
    Collection: "vote"
    """
    candidate_id: str = Field(..., description="ObjectId of the candidate document as string")
