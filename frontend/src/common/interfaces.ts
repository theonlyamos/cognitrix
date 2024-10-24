export interface MessageInterface {
  id?: string | number;
  role: string;
  type?: string;
  content: string;
  image?: string;
  thought?: string;
  observation?: string;
  mindspace?: string;
  reflection?: string;
  artifacts?: object[];
  tool_calls?: object[];
}

export interface AgentInterface extends Object {
  id: string;
  name: string;
  model: string;
  provider: string;
  tools?: string[];
}

export interface TaskInterface extends Object {
  id?: string;
  title: string;
  description: string;
  step_instructions?: object;
  assigned_agents: string[];
  status: string;
  autostart: boolean;
  done: boolean;
}

export interface SessionInterface {
  id: string;
  agent_id?: string;
  task_id?: string;
  team_id?: string;
  chat: object[];
  created_at: string;
  updated_at: string;
}

export interface ProviderInterface extends Object {
  provider: string;
  model: string;
  api_key: string;
  base_url?: string;
  temperature: Number;
  max_tokens?: Number;
  supports_system_prompt?: boolean;
  system_prompt?: string;
  is_multimodal: boolean;
  supports_tool_use: boolean;
  tools?: object[];
  chat_history?: object[];
  client?: string;
}

export interface ToolInterface extends Object {
  name: string;
  description: string;
  category: string;
  parameters: object;
}

export interface AgentDetailInterface extends Object {
  id?: string;
  name: string;
  system_prompt: string;
  is_sub_agent: boolean;
  parent_id?: string;
  llm: ProviderInterface;
  tools?: ToolInterface[];
  autostart: boolean;
  task?: object;
  websocket?: WebSocket;
  verbose?: boolean;
  created_at?: string;
}

export interface TaskDetailInterface extends Object {
  id?: string;
  title: string;
  description: string;
  step_instructions?: object;
  assigned_agents: string[];
  tools?: ToolInterface[];
  autostart: boolean;
  status: string;
  done: boolean;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  session_id?: string;
  team_id?: string;
}

export interface SSEMessage {
  type: string;
  content: any;
  action?: string;
  complete?: boolean;
}

export interface SSEState {
  event: string;
  message: SSEMessage | null;
  connected: boolean;
  error: Event | Error | null;
}

export interface TeamInterface {
  id?: string;
  name: string;
  assigned_agents: string[];
  description: string;
  leader_id?: string;
  created_at?: string;
  updated_at?: string;
}

export interface User {
  id?: string;
  name: string;
  email: string;
}

export interface LLMResponseInterface {
  id: string;
  created_at: string;
  updated_at: string;
  llm_response: string;
  chunks?: string[];
  current_chunk?: string;
  text?: string;
  result?: string;
  tool_calls?: null | any; // You might want to define a more specific type for tool_calls if needed
  artifacts?: Record<string, any>;
  observation?: null | string;
  thought?: null | string;
  mindspace?: null | string;
  reflection?: null | string;
  type?: string;
  before?: null | string;
  after?: null | string;
}
