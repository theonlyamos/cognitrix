from odbms import Model
from typing import List, Optional
from datetime import datetime

class User(Model):
    name: str
    email: str
    password: str

