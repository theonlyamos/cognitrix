import type { AgentDetailInterface } from "./interfaces";
import type { TaskDetailInterface } from "./interfaces";

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
