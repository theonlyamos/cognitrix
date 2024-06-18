from ...api.routes.base import api_router
from ...api.routes.public import public_api
from ...api.routes.agents import agents_api

api_router.include_router(public_api)
api_router.include_router(agents_api)