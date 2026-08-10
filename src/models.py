"""
Pydantic Data Models - Request/Response schemas for the API layer
These define the data contracts between the dashboard and the rest of the system.
"""
from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator


# ─── Task Models ───────────────────────────────────────────────────────────────

class TaskCreate(BaseModel):
    """Schema for creating a new task via the dashboard form"""
    prompt: str = Field(..., min_length=1, max_length=50_000, description="User prompt text")
    priority: int = Field(default=0, ge=0, le=10, description="Task priority (0=normal, 10=urgent)")

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Prompt cannot be blank")
        return v.strip()


class TaskResponse(BaseModel):
    """Schema for returning task data from the API"""
    id: int
    prompt: str
    status: str
    priority: int
    created_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    result_path: Optional[str]
    error_message: Optional[str]
    retry_count: int
    max_retries: int
    limit_reset_time: Optional[str]
    metadata: Optional[str]
    created_by: str

    model_config = {"from_attributes": True}


# ─── Claude Status Models ──────────────────────────────────────────────────────

class ClaudeStatusResponse(BaseModel):
    """Schema for Claude availability status"""
    available: bool
    reset_time: Optional[str]
    last_check: Optional[str]
    last_limit_message: Optional[str]
    total_requests_today: int

    model_config = {"from_attributes": True}


# ─── Statistics Model ──────────────────────────────────────────────────────────

class SystemStats(BaseModel):
    """System-wide statistics for the dashboard"""
    total_tasks: int = 0
    task_counts: Dict[str, int] = Field(default_factory=dict)
    requests_today: int = 0
    avg_completion_minutes: float = 0.0
    claude_available: bool = True
    reset_time: Optional[str] = None


# ─── API Response Wrappers ─────────────────────────────────────────────────────

class SuccessResponse(BaseModel):
    """Generic success response"""
    success: bool = True
    message: str
    data: Optional[Any] = None


class ErrorResponse(BaseModel):
    """Generic error response"""
    success: bool = False
    error: str
    detail: Optional[str] = None
