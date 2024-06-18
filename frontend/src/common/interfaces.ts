export interface MessageInterface {
    id?: string|number,
    role: string|String,
    content: String,
    image?: string
}

export interface AgentInterface extends Object {
    id: String,
    name: String,
    model: String,
    provider: String,
    tools?: String[]
}