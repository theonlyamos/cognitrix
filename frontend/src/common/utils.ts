import type { AgentDetailInterface } from "./interfaces";

const API_VERSION = 'v1';
export const BACKEND_URI = `${import.meta.env.VITE_BACKEND_URL}/api/${API_VERSION}`

export const sendChatMessage = async(query: String): Promise<string> => {
    const response = await fetch(`${BACKEND_URI}/?query=${query}`)
    return response.json()
}

export const getAllAgents = async(): Promise<Object[]> => {
    const response = await fetch(`${BACKEND_URI}/agents`)
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

export const getAgent = async(agent_id: string): Promise<Object> => {
    const response = await fetch(`${BACKEND_URI}/agents/${agent_id}`)
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