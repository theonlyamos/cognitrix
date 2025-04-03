import { XMLParser } from 'fast-xml-parser';
import type {
  AgentDetailInterface,
  TaskDetailInterface,
  TeamInterface,
  SessionInterface  // Add this import
} from "./interfaces";
import { API_BACKEND_URI, DEEPGRAM_API_KEY } from './constants';
import axios from 'axios';

const api = axios.create({
  baseURL: API_BACKEND_URI,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');

  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
}, (error) => {
  return Promise.reject(error);
});

export const sendChatMessage = async (query: String): Promise<string> => {
  const response = await api.get(`/?query=${query}`)
  return response.data
}

export const generatePrompt = async (agentName = '', prompt: string): Promise<Object> => {
  const response = await api.post('/generate', { agentName, prompt });
  return response.data;
}

export const getAllAgents = async (): Promise<Object[]> => {
  const response = await api.get('/agents');
  return response.data;
}

export const getAgent = async (agentId: string): Promise<Object> => {
  const response = await api.get(`/agents/${agentId}`);
  return response.data;
}

export const saveAgent = async (agent: AgentDetailInterface): Promise<Object> => {
  const response = await api.post('/agents', agent);
  return response.data;
}

export const getAgentSession = async (agentId: string): Promise<Object> => {
  const response = await api.get(`/agents/${agentId}/session`);
  return response.data;
}

export const getLLMProviders = async (): Promise<Object[]> => {
  const response = await api.get('/providers');
  return response.data;
}

export const getTools = async (): Promise<Object[]> => {
  const response = await api.get('/tools');
  return response.data;
}

export const getAllTasks = async (): Promise<TaskDetailInterface[]> => {
  const response = await api.get('/tasks');
  return response.data;
}

export const getTask = async (taskId: string): Promise<Object> => {
  const response = await api.get(`/tasks/${taskId}`);
  return response.data;
}

export const saveTask = async (task: TaskDetailInterface): Promise<Object> => {
  const response = await api.post('/tasks', task);
  return response.data;
}

export const deleteTask = async (taskId: string): Promise<Object> => {
  const response = await api.delete(`/tasks/${taskId}`);
  return response.data;
}

export const getTaskSession = async (sessionId: string): Promise<SessionInterface> => {
  const response = await api.get(`/sessions/${sessionId}`);
  if (response.status !== 200) {
    throw new Error('Failed to fetch session');
  }
  return response.data;
};

export const updateTaskStatus = async (task_id: any): Promise<Object> => {
  const response = await api.get(`/tasks/start/${task_id}`)
  return response.data
}

export const convertXmlToJson = (xmlText: string) => {
  const parser = new XMLParser({
    ignoreAttributes: false,
    attributeNamePrefix: "@_",
    textNodeName: "#text",
    parseAttributeValue: true,
    trimValues: true,
  });

  try {
    const wrappedXml = `<root>${xmlText}</root>`;
    const result = parser.parse(wrappedXml);

    // Helper function to clean up the parsed result
    const cleanObject = (obj: any): any => {
      if (typeof obj !== 'object' || obj === null) {
        return obj;
      }

      if (Array.isArray(obj)) {
        return obj.map(cleanObject);
      }

      const cleaned: any = {};
      for (const [key, value] of Object.entries(obj)) {
        if (key !== '#text' || value !== '') {
          cleaned[key.replace('@_', '')] = cleanObject(value);
        }
      }

      // If the object only has a #text property, return its value
      if (Object.keys(cleaned).length === 1 && '#text' in cleaned) {
        return cleaned['#text'];
      }

      return cleaned;
    };

    return cleanObject(result.root);
  } catch (error) {
    console.error('Error parsing XML:', error);
    return null;
  }
};

export const startMicrophoneStream = async (): Promise<MediaStream> => {
  return await navigator.mediaDevices.getUserMedia({ audio: true });
}

export async function getAllTeams(): Promise<TeamInterface[]> {
  const response = await api.get(`/teams`);
  if (response.status !== 200) {
    throw new Error('Failed to fetch teams');
  }
  return response.data
}

export async function getTeam(teamId: string): Promise<TeamInterface> {
  const response = await api.get(`/teams/${teamId}`);
  if (response.status !== 200) {
    throw new Error('Failed to fetch team');
  }
  return response.data
}

export async function saveTeam(team: TeamInterface): Promise<TeamInterface> {
  const response = await api.post(`/teams`, team);
  if (response.status !== 200) {
    throw new Error('Failed to save team');
  }
  return response.data;
}

export async function deleteTeam(teamId: string): Promise<void> {
  const response = await api.delete(`/teams/${teamId}`);

  if (response.status !== 200) {
    throw new Error('Failed to delete team');
  }
  return response.data
}

export async function generateTeam(description: string): Promise<Partial<TeamInterface>> {
  // Call your AI service or API here to generate team details
  // This is a placeholder implementation
  const response = await fetch('/api/generate-team', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ description }),
  });

  if (!response.ok) {
    throw new Error('Failed to generate team');
  }

  return await response.json();
}

export async function getTasksByTeam(teamId: string): Promise<TaskDetailInterface[]> {
  const response = await api.get(`/teams/${teamId}/tasks`);
  if (response.status !== 200) {
    throw new Error('Failed to fetch tasks for team');
  }
  return response.data;
}

export async function createSession(session: Object = {}): Promise<SessionInterface> {
  const response = await api.post(`/sessions`, session);
  if (response.status !== 200) {
    throw new Error('Failed to create session');
  }
  return response.data;
}

export async function getAllSessions(): Promise<SessionInterface[]> {
  const response = await api.get(`/sessions`);
  if (response.status !== 200) {
    throw new Error('Failed to fetch sessions');
  }
  return response.data;
}

export async function getSession(sessionId: string): Promise<SessionInterface> {
  const response = await api.get(`/sessions/${sessionId}`);
  if (response.status !== 200) {
    throw new Error('Failed to fetch session');
  }
  return response.data;
}

export async function getSessionsByTeam(teamId: string): Promise<SessionInterface[]> {
  const response = await api.get(`/sessions/teams/${teamId}`);
  if (response.status !== 200) {
    throw new Error('Failed to fetch sessions for team');
  }
  return response.data;
}

export async function getSessionsByTask(taskId: string): Promise<SessionInterface[]> {
  const response = await api.get(`/sessions/tasks/${taskId}`);
  if (response.status !== 200) {
    throw new Error('Failed to fetch sessions for task');
  }
  return response.data;
}

export async function getSessionsByAgent(agentId: string): Promise<SessionInterface[]> {
  const response = await api.get(`/sessions/agents/${agentId}`);
  if (response.status !== 200) {
    throw new Error('Failed to fetch sessions for agent');
  }
  return response.data;
}

export async function deleteSession(sessionId: string): Promise<void> {
  const response = await api.delete(`/sessions/${sessionId}`);
  if (response.status !== 200) {
    throw new Error('Failed to delete session');
  }
  return response.data;
}

export async function deleteChat(sessionId: string): Promise<void> {
  const response = await api.delete(`/sessions/${sessionId}/chat`);
  if (response.status !== 200) {
    throw new Error('Failed to delete chat');
  }
  return response.data;
}


