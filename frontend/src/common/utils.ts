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