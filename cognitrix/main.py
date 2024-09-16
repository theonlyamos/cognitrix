from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from cognitrix.api.routes.auth import auth_api as auth_router, get_current_user
from cognitrix.config import initialize_database

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize the database
@app.on_event("startup")
async def startup_event():
    initialize_database()

# Include the authentication routes
app.include_router(auth_router, prefix="/auth", tags=["auth"])

# Example of a protected route
@app.get("/protected")
async def protected_route(current_user: dict = Depends(get_current_user)):
    return {"message": "This is a protected route", "user": current_user}

# Your other routes and application logic go here