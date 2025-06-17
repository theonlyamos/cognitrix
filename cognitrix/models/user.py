from odbms import Model


class User(Model):
    name: str
    email: str
    password: str

