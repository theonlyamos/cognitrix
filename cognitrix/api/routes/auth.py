from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Annotated, Optional

from cognitrix.models import User
from cognitrix.common.security import (
    authenticate, get_current_user, create_access_token, 
    Token
)
from cognitrix.common.constants import JWT_ACCESS_TOKEN_EXPIRE_MINUTES
from cognitrix.common.utils import Utils


auth_api = APIRouter(
    prefix='/auth'
)
   
# Secret key for JWT encoding/decoding


class LoginForm(BaseModel):
    username: str
    password: str


@auth_api.post("/login", response_model=Token)
async def login(form_data: LoginForm):
    user = authenticate(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=float(JWT_ACCESS_TOKEN_EXPIRE_MINUTES))
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )

    user_data = user.model_dump()

    return JSONResponse({"user": user_data, "access_token": access_token, "token_type": "bearer"})

@auth_api.post("/signup")
async def signup(new_user: User):
    existing_user = User.find_one({'email': new_user.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    hashed_password = Utils.hash_password(new_user.password)
    new_user.password = hashed_password
    
    new_user.save()
    return {"message": "User created successfully"}

@auth_api.get("/user")
async def get_user(user: Annotated[User, Depends(get_current_user)]):
    return user

@auth_api.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    # In a real application, you might want to invalidate the token here
    return {"message": "Logged out successfully"}