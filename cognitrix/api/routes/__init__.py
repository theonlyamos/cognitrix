from ...api.routes.agents import agents_api, agents_invoke_api
from ...api.routes.api_keys import api_keys_api
from ...api.routes.auth import auth_api
from ...api.routes.base import api_router
from ...api.routes.providers import providers_api
from ...api.routes.public import public_api
from ...api.routes.sessions import sessions_api
from ...api.routes.skills import skills_api
from ...api.routes.tasks import tasks_api, tasks_run_api
from ...api.routes.teams import teams_api, teams_run_api
from ...api.routes.tools import tools_api

api_router.include_router(public_api)
# Invoke/run-scoped routers MUST register before their crud twins: FastAPI
# matches in registration order and e.g. GET /tasks/start/{id} would otherwise
# be swallowed by GET /tasks/{task_id}.
api_router.include_router(agents_invoke_api)
api_router.include_router(agents_api)
api_router.include_router(tools_api)
api_router.include_router(skills_api)
api_router.include_router(providers_api)
api_router.include_router(tasks_run_api)
api_router.include_router(tasks_api)
api_router.include_router(teams_run_api)
api_router.include_router(teams_api)
api_router.include_router(sessions_api)
api_router.include_router(auth_api)
api_router.include_router(api_keys_api)
