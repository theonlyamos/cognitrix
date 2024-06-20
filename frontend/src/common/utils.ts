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

export const getAgent = async(agent_id: String): Promise<Object> => {
    const response = await fetch(`${BACKEND_URI}/agents/${agent_id}`)
    return response.json()
}