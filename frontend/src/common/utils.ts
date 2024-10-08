import { XMLParser } from 'fast-xml-parser';
import type { 
    AgentDetailInterface, 
    TaskDetailInterface, 
    TeamInterface 
} from "./interfaces";
import { API_BACKEND_URI } from './constants';
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

export const sendChatMessage = async(query: String): Promise<string> => {
    const response = await api.get(`${API_BACKEND_URI}/?query=${query}`)
    return response.data
}

export const generatePrompt = async(agentName='', prompt: string): Promise<Object> => {
  const response = await api.post('/generate', { agentName, prompt });
  return response.data;
}

export const getAllAgents = async(): Promise<Object[]> => {
  const response = await api.get('/agents');
  return response.data;
}

export const getAgent = async(agentId: string): Promise<Object> => {
  const response = await api.get(`/agents/${agentId}`);
  return response.data;
}

export const saveAgent = async(agent: AgentDetailInterface): Promise<Object> => {
  const response = await api.post('/agents', agent);
  return response.data;
}

export const getAgentSession = async(agentId: string): Promise<Object> => {
  const response = await api.get(`/agents/${agentId}/session`);
  return response.data;
}

export const getLLMProviders = async(): Promise<Object[]> => {
  const response = await api.get('/llms');
  return response.data;
}

export const getTools = async(): Promise<Object[]> => {
  const response = await api.get('/tools');
  return response.data;
}

export const getAllTasks = async(): Promise<Object[]> => {
  const response = await api.get('/tasks');
  return response.data;
}

export const getTask = async(taskId: string): Promise<Object> => {
  const response = await api.get(`/tasks/${taskId}`);
  return response.data;
}

export const saveTask = async(task: TaskDetailInterface): Promise<Object> => {
  const response = await api.post('/tasks', task);
  return response.data;
}

export const getTaskSession = async(taskId: string): Promise<Object> => {
  const response = await api.get(`/tasks/${taskId}/session`);
  return response.data;
}

export const updateTaskStatus = async(task_id: any): Promise<Object> => {
    const response = await api.get(`${API_BACKEND_URI}/tasks/start/${task_id}`)
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

export async function getAllTeams(): Promise<TeamInterface[]> {
    const response = await api.get(`${API_BACKEND_URI}/teams`);
    if (response.status !== 200) {
      throw new Error('Failed to fetch teams');
    }
    return response.data
  }
  
  export async function getTeam(teamId: string): Promise<TeamInterface> {
    const response = await api.get(`${API_BACKEND_URI}/teams/${teamId}`);
    if (response.status !== 200) {
      throw new Error('Failed to fetch team');
    }
    return response.data
  }
  
export async function saveTeam(team: TeamInterface): Promise<TeamInterface> {
    const response = await api.post(`${API_BACKEND_URI}/teams`, team);
    if (response.status !== 200) {
      throw new Error('Failed to save team');
    }
    return response.data;
  }
  
  export async function deleteTeam(teamId: string): Promise<void> {
    const response = await api.delete(`${API_BACKEND_URI}/teams/${teamId}`);
  
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
  const response = await api.get(`${API_BACKEND_URI}/teams/${teamId}/tasks`);
  if (response.status !== 200) {
    throw new Error('Failed to fetch tasks for team');
  }
  return response.data;
}