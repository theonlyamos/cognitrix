from ...api.routes.account import account_api
from ...api.routes.admin import admin_api
from ...api.routes.agents import agents_api, agents_invoke_api
from ...api.routes.api_keys import api_keys_api
from ...api.routes.mcp import mcp_api
from ...api.routes.connected_apps import apps_api
from ...api.routes.credentials import credentials_api
from ...api.routes.knowledge import knowledge_api
from ...api.routes.memories import memories_api
from ...api.routes.themes import themes_api
from ...api.routes.swarms import swarms_api
from ...api.routes.board import board_api
from ...api.routes.artifacts import artifacts_api
from ...api.routes.artifact_pages import artifact_frame_api, artifact_pages_api
from ...api.routes.produced_files import produced_file_content_api, produced_files_api
from ...api.routes.app_views import app_view_frame_api, app_views_api
from ...api.routes.assistant import assistant_api
from ...api.routes.proactive import proactive_api
from ...api.routes.automations import automations_api, hooks_api
from ...api.routes.github_watches import github_hooks_api, github_watches_api
from ...api.routes.audio import audio_api
from ...api.routes.auth import auth_api
from ...api.routes.sign_ins import sign_ins_api
from ...api.routes.agent_email import email_api
from ...api.routes.channels import channel_hooks_api, channels_api
from ...api.routes.meetings import meetings_api
from ...api.routes.payments import payments_api
from ...api.routes.computer import computer_api
from ...api.routes.computer_overlay import overlay_api
from ...api.routes.computer_sandbox import sandbox_api
from ...api.routes.computer_devices import devices_api, link_api
from ...api.routes.projects import projects_api
from ...api.routes.oauth import oauth_api
from ...api.routes.onboarding import onboarding_api
from ...api.routes.whats_new import whats_new_api
from ...api.routes.folders import folders_api
from ...api.routes.hosted_apps import app_access_api, hosted_apps_api
from ...api.routes.base import api_router
from ...api.routes.app_settings import app_settings_api
from ...api.routes.server_setup import server_setup_api
from ...api.routes.providers import providers_api
from ...api.routes.public import public_api
from ...api.routes.public_agents import public_agent_api, public_links_api
from ...api.routes.chat_sharing import chat_sharing_api
from ...api.routes.sessions import sessions_api
from ...api.routes.skills import skills_api
from ...api.routes.system import system_api
from ...api.routes.tasks import tasks_api, tasks_run_api
from ...api.routes.team_chat import team_chat_api
from ...api.routes.teams import teams_api, teams_run_api
from ...api.routes.tools import tools_api
from ...api.routes.push import push_api
from ...api.routes.usage import usage_api
from ...api.routes.audit import audit_api

api_router.include_router(public_api)
api_router.include_router(public_agent_api)
api_router.include_router(public_links_api)
api_router.include_router(artifact_frame_api)
api_router.include_router(produced_file_content_api)
api_router.include_router(app_view_frame_api)
api_router.include_router(hooks_api)
api_router.include_router(github_hooks_api)
# Invoke/run-scoped routers MUST register before their crud twins: FastAPI
# matches in registration order and e.g. GET /tasks/start/{id} would otherwise
# be swallowed by GET /tasks/{task_id}.
api_router.include_router(assistant_api)
api_router.include_router(proactive_api)
api_router.include_router(agents_invoke_api)
api_router.include_router(agents_api)
api_router.include_router(tools_api)
api_router.include_router(artifacts_api)
api_router.include_router(artifact_pages_api)
api_router.include_router(produced_files_api)
api_router.include_router(app_views_api)
api_router.include_router(hosted_apps_api)
api_router.include_router(app_access_api)
api_router.include_router(skills_api)
api_router.include_router(providers_api)
api_router.include_router(app_settings_api)
api_router.include_router(server_setup_api)
api_router.include_router(automations_api)
api_router.include_router(github_watches_api)
api_router.include_router(tasks_run_api)
api_router.include_router(tasks_api)
api_router.include_router(teams_run_api)
api_router.include_router(team_chat_api)
api_router.include_router(teams_api)
api_router.include_router(chat_sharing_api)
api_router.include_router(sessions_api)
api_router.include_router(sign_ins_api)
api_router.include_router(auth_api)
api_router.include_router(oauth_api)
api_router.include_router(onboarding_api)
api_router.include_router(whats_new_api)
api_router.include_router(folders_api)
api_router.include_router(audio_api)
api_router.include_router(api_keys_api)
api_router.include_router(mcp_api)
api_router.include_router(apps_api)
api_router.include_router(credentials_api)
api_router.include_router(memories_api)
api_router.include_router(themes_api)
api_router.include_router(swarms_api)
api_router.include_router(board_api)
api_router.include_router(usage_api)
api_router.include_router(audit_api)
api_router.include_router(push_api)
api_router.include_router(account_api)
api_router.include_router(knowledge_api)
api_router.include_router(admin_api)
api_router.include_router(system_api)
api_router.include_router(channel_hooks_api)
api_router.include_router(channels_api)
api_router.include_router(email_api)
api_router.include_router(meetings_api)
api_router.include_router(payments_api)
api_router.include_router(computer_api)
api_router.include_router(overlay_api)
api_router.include_router(sandbox_api)
api_router.include_router(devices_api)
api_router.include_router(projects_api)
api_router.include_router(link_api)
