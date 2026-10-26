from typing import Optional
from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    text: Optional[str] = None
    code: Optional[str] = None


class NewSessionRequest(BaseModel):
    prompt: str
    enable_supports: Optional[bool] = None


class EditSessionRequest(BaseModel):
    prompt: str
    enable_supports: Optional[bool] = None


class OpenFolderRequest(BaseModel):
    version: Optional[int] = None


class PrintRequest(BaseModel):
    version: Optional[int] = None
    ams_slot: Optional[int] = None
    enable_supports: Optional[bool] = None
