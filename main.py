import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from bson.objectid import ObjectId

from database import db, create_document, get_documents
from schemas import Voter, VoterStatus, Candidate, Vote

app = FastAPI(title="UNB Presidential Election API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------- Pydantic request/response models (separate from DB schemas) ---------
class ValidateRequest(BaseModel):
    nim: str = Field(..., min_length=8, max_length=20)

class ValidateResponse(BaseModel):
    nim: str
    eligible: bool
    has_voted: bool

class VoteRequest(BaseModel):
    nim: str = Field(..., min_length=8, max_length=20)
    candidate_id: str = Field(..., description="Candidate document ObjectId as string")

class CandidatePublic(BaseModel):
    id: str
    number: int
    president_name: str
    vice_name: str
    vision: Optional[str] = None
    mission: Optional[List[str]] = None
    photo_url: Optional[str] = None

class ResultsItem(BaseModel):
    candidate_id: str
    number: int
    president_name: str
    vice_name: str
    count: int
    percentage: float

class ResultsResponse(BaseModel):
    total_votes: int
    results: List[ResultsItem]


# ---------------------------- Utility functions ----------------------------

def objid(oid: str) -> ObjectId:
    try:
        return ObjectId(oid)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid candidate_id")


def to_public_candidate(doc) -> CandidatePublic:
    return CandidatePublic(
        id=str(doc.get("_id")),
        number=doc.get("number"),
        president_name=doc.get("president_name"),
        vice_name=doc.get("vice_name"),
        vision=doc.get("vision"),
        mission=doc.get("mission"),
        photo_url=doc.get("photo_url"),
    )


# --------------------------------- Routes ---------------------------------
@app.get("/", tags=["system"])
def read_root():
    return {"message": "UNB Presidential Election API is running"}


@app.get("/test", tags=["system"])
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    return response


# Public endpoints
@app.get("/api/candidates", response_model=List[CandidatePublic], tags=["public"])
def list_candidates():
    docs = get_documents("candidate")
    docs_sorted = sorted(docs, key=lambda d: d.get("number", 0))
    return [to_public_candidate(d) for d in docs_sorted]


@app.post("/api/voter/validate", response_model=ValidateResponse, tags=["public"])
def validate_voter(payload: ValidateRequest):
    nim = payload.nim
    voter = db["voter"].find_one({"nim": nim})
    eligible = bool(voter and voter.get("eligible", True))

    status = db["voterstatus"].find_one({"nim": nim})
    has_voted = bool(status and status.get("has_voted", False))

    if not voter:
        # If not present, treat as ineligible by default
        return ValidateResponse(nim=nim, eligible=False, has_voted=False)

    return ValidateResponse(nim=nim, eligible=eligible, has_voted=has_voted)


@app.post("/api/vote", tags=["public"])
def cast_vote(payload: VoteRequest):
    nim = payload.nim
    candidate_id = payload.candidate_id

    # 1) Check voter eligibility
    voter = db["voter"].find_one({"nim": nim})
    if not voter or not voter.get("eligible", True):
        raise HTTPException(status_code=403, detail="NIM is not eligible to vote")

    # 2) Enforce one-vote-per-NIM
    status = db["voterstatus"].find_one({"nim": nim})
    if status and status.get("has_voted", False):
        raise HTTPException(status_code=409, detail="This NIM has already voted")

    # 3) Candidate must exist
    candidate = db["candidate"].find_one({"_id": objid(candidate_id)})
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # 4) Record anonymous vote (no NIM stored)
    vote_doc = Vote(candidate_id=candidate_id)
    create_document("vote", vote_doc)

    # 5) Mark voter status as voted with timestamp
    db["voterstatus"].update_one(
        {"nim": nim},
        {"$set": {"nim": nim, "has_voted": True, "voted_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )

    return {"success": True}


@app.get("/api/results", response_model=ResultsResponse, tags=["public"])
def get_results():
    # Get all candidates
    candidates = list(db["candidate"].find({}))
    counts_map = {str(c["_id"]): 0 for c in candidates}

    # Count votes grouped by candidate_id
    pipeline = [
        {"$group": {"_id": "$candidate_id", "count": {"$sum": 1}}}
    ]
    for row in db["vote"].aggregate(pipeline):
        cid = row["_id"]
        if isinstance(cid, ObjectId):
            cid = str(cid)
        counts_map[str(cid)] = row["count"]

    total_votes = sum(counts_map.values())
    results: List[ResultsItem] = []
    for c in candidates:
        cid = str(c["_id"])
        count = counts_map.get(cid, 0)
        percentage = (count / total_votes * 100.0) if total_votes > 0 else 0.0
        results.append(
            ResultsItem(
                candidate_id=cid,
                number=c.get("number"),
                president_name=c.get("president_name"),
                vice_name=c.get("vice_name"),
                count=count,
                percentage=round(percentage, 2),
            )
        )

    # Sort by ballot number
    results.sort(key=lambda r: r.number)
    return ResultsResponse(total_votes=total_votes, results=results)


# ----------------------- Optional demo seed endpoint -----------------------
class SeedRequest(BaseModel):
    create_candidates: bool = True
    create_voters: bool = True

@app.post("/api/seed-demo", tags=["system"])
def seed_demo(data: SeedRequest):
    # Avoid reseeding if already present
    if data.create_candidates and db["candidate"].count_documents({}) == 0:
        demo_candidates = [
            {
                "number": 1,
                "president_name": "Andi Pratama",
                "vice_name": "Siti Lestari",
                "vision": "Transparansi dan inovasi kampus.",
                "mission": ["Digitalisasi layanan", "Pemberdayaan UKM"],
                "photo_url": "https://picsum.photos/seed/cand1/400/400",
            },
            {
                "number": 2,
                "president_name": "Budi Santoso",
                "vice_name": "Rina Kartika",
                "vision": "Kampus inklusif dan berprestasi.",
                "mission": ["Akses pendidikan merata", "Program beasiswa"],
                "photo_url": "https://picsum.photos/seed/cand2/400/400",
            },
        ]
        for c in demo_candidates:
            db["candidate"].insert_one({**c, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)})

    if data.create_voters and db["voter"].count_documents({}) == 0:
        demo_voters = [
            {"nim": "20200001", "name": "Mahasiswa 1", "program": "Teknik", "eligible": True},
            {"nim": "20200002", "name": "Mahasiswa 2", "program": "Manajemen", "eligible": True},
            {"nim": "20200003", "name": "Mahasiswa 3", "program": "Informatika", "eligible": True},
        ]
        for v in demo_voters:
            db["voter"].insert_one({**v, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)})

    return {"seeded": True}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
