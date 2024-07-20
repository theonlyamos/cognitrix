export interface MessageInterface {
    id?: string|number,
    role: string|string,
    content: string,
    image?: string
}

export interface AgentInterface extends Object {
    id: string,
    name: string,
    model: string,
    provider: string,
    tools?: string[]
}

export interface SessionInterface {
    id: string,
    agent_id: string,
    chat: Object[],
    datetime: string
}

export interface ProviderInterface extends Object {
    provider: string,
    model: string,
    api_key: string,
    base_url?: string,
    temperature: Number,
    max_tokens?: Number,
    supports_system_prompt?: boolean,
    system_prompt?: string,
    is_multimodal: boolean,
    supports_tool_use: boolean,
    tools?: Object[],
    chat_history?: Object[],
    client?: string,
}

export interface ToolInterface extends Object {
    name: string,
    description: string,
    category: string,
    parameters: Object
}

export interface AgentDetailInterface extends Object {
    id?: string,
    name: string,
    prompt_template: string,
    is_sub_agent: boolean,
    parent_id?: string,
    llm: ProviderInterface,
    tools?: ToolInterface[],
    autostart: boolean,
    task?: Object,
    websocket?: WebSocket,
    verbose?: boolean
}

export interface SSEMessage {
    type: string;
    content: any;
    action?: string;
    complete?: boolean;
}

export interface SSEState {
    event: string,
    message: SSEMessage | null;
    connected: boolean;
    error: Event | Error | null;
}
  