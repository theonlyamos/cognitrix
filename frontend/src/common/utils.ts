import { XMLParser } from 'fast-xml-parser';
import type { 
    AgentDetailInterface, 
    TaskDetailInterface, 
    TeamInterface 
} from "./interfaces";


const API_VERSION = 'v1';
export const BACKEND_URI = `${import.meta.env.VITE_BACKEND_URL}/api/${API_VERSION}`

export const sendChatMessage = async(query: String): Promise<string> => {
    const response = await fetch(`${BACKEND_URI}/?query=${query}`)
    return response.json()
}

export const generatePrompt = async(agentName='', prompt: string): Promise<Object> => {
    const response = await fetch(`${BACKEND_URI}/generate`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({agentName, prompt})
    })
    return response.json()
}

export const getAllAgents = async(): Promise<Object[]> => {
    const response = await fetch(`${BACKEND_URI}/agents`)
    return response.json()
}

export const getAgent = async(agentId: string): Promise<Object> => {
    const response = await fetch(`${BACKEND_URI}/agents/${agentId}`)
    return response.json()
}

export const saveAgent = async(agent: AgentDetailInterface): Promise<Object> => {
    const response = await fetch(`${BACKEND_URI}/agents`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({...agent})
    })
    return response.json()
}

export const getAgentSession = async(agentId: string): Promise<Object> => {
    const response = await fetch(`${BACKEND_URI}/agents/${agentId}/session`)
    return response.json()
}

export const getLLMProviders = async(): Promise<Object[]> => {
    const response = await fetch(`${BACKEND_URI}/llms`)
    return response.json()
}

export const getTools = async(): Promise<Object[]> => {
    const response = await fetch(`${BACKEND_URI}/tools`)
    return response.json()
}

export const getAllTasks = async(): Promise<Object[]> => {
    const response = await fetch(`${BACKEND_URI}/tasks`)
    return response.json()
}

export const getTask = async(taskId: string): Promise<Object> => {
    const response = await fetch(`${BACKEND_URI}/tasks/${taskId}`)
    return response.json()
}

export const saveTask = async(task: TaskDetailInterface): Promise<Object> => {
    const response = await fetch(`${BACKEND_URI}/tasks`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({...task})
    })
    return response.json()
}

export const getTaskSession = async(taskId: string): Promise<Object> => {
    const response = await fetch(`${BACKEND_URI}/tasks/${taskId}/session`)
    return response.json()
}

export const updateTaskStatus = async(task_id: any): Promise<Object> => {
    const response = await fetch(`${BACKEND_URI}/tasks/start/${task_id}`)
    return response.json()
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
    const response = await fetch(`${BACKEND_URI}/teams`);
    if (!response.ok) {
      throw new Error('Failed to fetch teams');
    }
    return response.json();
  }
  
  export async function getTeam(teamId: string): Promise<TeamInterface> {
    const response = await fetch(`${BACKEND_URI}/teams/${teamId}`);
    if (!response.ok) {
      throw new Error('Failed to fetch team');
    }
    return response.json();
  }
  
export async function saveTeam(team: TeamInterface): Promise<TeamInterface> {
    const method = team.id ? 'PUT' : 'POST';
    const url = team.id ? `${BACKEND_URI}/teams/${team.id}` : `${BACKEND_URI}/teams`;
    
    const response = await fetch(url, {
      method,
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(team),
    });
  
    if (!response.ok) {
      throw new Error('Failed to save team');
    }
    return response.json();
  }
  
  export async function deleteTeam(teamId: string): Promise<void> {
    const response = await fetch(`${BACKEND_URI}/teams/${teamId}`, {
      method: 'DELETE',
    });
  
    if (!response.ok) {
      throw new Error('Failed to delete team');
    }
}