from ...api.routes.base import api_router
from ...api.routes.public import public_api
from ...api.routes.agents import agents_api
from ...api.routes.tools import tools_api
from ...api.routes.providers import providers_api
from ...api.routes.tasks import tasks_api
from ...api.routes.teams import teams_api
from ...api.routes.auth import auth_api
from ...api.routes.sessions import sessions_api

api_router.include_router(public_api)
api_router.include_router(agents_api)
api_router.include_router(tools_api)
api_router.include_router(providers_api)
api_router.include_router(tasks_api)
api_router.include_router(teams_api)
api_router.include_router(sessions_api)
api_router.include_router(auth_api)