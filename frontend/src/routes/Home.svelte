<script lang="ts">
    import { onDestroy, onMount } from 'svelte';
    import type { MessageInterface, SessionInterface } from '../common/interfaces';
    import { BACKEND_URI } from '../common/utils';
    import ChatComponent from '../lib/ChatContent.svelte'
    import InputBar from '../lib/Inputbar.svelte';
    import { navigate } from 'svelte-routing';

    export let session_id: string = '';
    export let agent_id: string = '';
    
    let sessions: SessionInterface[] = [];
    let messages: MessageInterface[] = [];
    let agentName: string = 'Assistant';
    let loading: boolean = true;
    let socket: any;

    const uploadFile = ()=>{
        console.log('Uploading file...')
    }

    const sendMessage = async(query: string)=>{
        loading = true;
        messages = [...messages, {
            role: 'user',
            content: query
        }]

        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({type: "chat_message", action: "send", content: query}));
        }
    }

    const resetState = () => {
        messages = [];
        agentName = 'Assistant';
        loading = true;
    }

    const handleRouteChange = () => {
        if (socket && socket.readyState === WebSocket.OPEN) {
            if (agent_id) {
                socket.send(JSON.stringify({type: "sessions", action: "get", agent_id: agent_id}));
            } else if (session_id) {
                socket.send(JSON.stringify({type: "chat_history", action: "get", session_id: session_id}));
            }
        }
    }

    const startWebSocketConnection = ()=>{
        const websocketUrl = new URL(BACKEND_URI.replace('http', 'ws')).origin;
        socket = new WebSocket(websocketUrl + '/ws');
        
        socket.onopen = () => {
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({type: "sessions", action: "list"}));
                handleRouteChange();
            }
        };

        socket.onmessage = (event: MessageEvent) => {
            loading = false
            let data = JSON.parse(event.data)
            
            if (data.type === 'chat_history') {
                agentName = data.agent_name
                for (let msg of data.content) {
                    messages = [...messages, {
                        role: msg.role.toLowerCase(),
                        content: msg.message
                    }]
                }
            }
            else if (data.type === 'chat_reply') {
                messages = [...messages, {
                    role: agentName,
                    content: data.content
                }]
            }
            else if (data.type === 'sessions') {
                console.log(data)
                if (data.action === 'list') {
                    sessions = data.content as SessionInterface[];
                }
                else if (data.action === 'get') {
                    session_id = data.session?.id
                }
            }
        };

        socket.onclose = () => {
            socket = null;
        };
    }

    onMount(() => {
        startWebSocketConnection();

        return (()=>{
            if (socket)
                socket.close()
        })
    });

    onDestroy(()=>{
        if (socket){
            socket.close()
        }
    })

    $: if (agent_id || session_id) {
        resetState();
        handleRouteChange();
    }
</script>

<ChatComponent {messages} {sessions}>
    <InputBar {uploadFile} {sendMessage} {loading}/>
</ChatComponent>