from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import threading
import time
import uvicorn
import os

from blocker import Blocker

app = FastAPI()

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Blocker
blocker = Blocker()
# Start blocker loop in background
@app.on_event("startup")
async def startup_event():
    blocker.start()

@app.on_event("shutdown")
async def shutdown_event():
    blocker.stop()

class Rule(BaseModel):
    type: str # "domain" or "application"
    value: str

@app.get("/rules")
def get_rules():
    return blocker.get_rules()

@app.post("/rules")
def add_rule(rule: Rule):
    blocker.add_rule(rule.type, rule.value)
    return {"status": "added", "rule": rule}

@app.delete("/rules")
def delete_rule(rule: Rule):
    blocker.remove_rule(rule.type, rule.value)
    return {"status": "removed", "rule": rule}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

