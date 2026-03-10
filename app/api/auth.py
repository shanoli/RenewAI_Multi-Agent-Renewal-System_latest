"""
Auth API — JWT login with email as primary key.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
import aiosqlite
from app.core.security import hash_password, verify_password, create_access_token
from app.core.config import get_settings
from app.utils.logger import logger

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])


class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str
    role: str = "agent"


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    name: str
    role: str


@router.post("/register", summary="Register (Password-less setup)")
async def register(req: RegisterRequest):
    try:
        async with aiosqlite.connect(settings.sqlite_db_path) as db:
            cursor = await db.execute("SELECT email FROM users WHERE email=?", (req.email,))
            existing = await cursor.fetchone()
            if existing:
                raise HTTPException(status_code=400, detail="Email already registered")
            
            # Passwords are not considered for now
            dummy_hash = "not_checked"

            await db.execute(
                "INSERT INTO users (email, name, hashed_password, role) VALUES (?, ?, ?, ?)",
                (req.email, req.name, dummy_hash, req.role)
            )
            await db.commit()
        return {"message": "User registered successfully", "email": req.email}
    except Exception as e:
        logger.error(f"❌ Registration error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))




@router.post("/login", response_model=TokenResponse, summary="Login (POC: Any email accepted)")
async def login(req: LoginRequest):
    # POC: Accept any email without checking database
    # Extract name from email (before @)
    name = req.email.split('@')[0].replace('.', ' ').title()
    
    token = create_access_token({"sub": req.email, "role": "agent"})
    return TokenResponse(access_token=token, name=name, role="agent")

