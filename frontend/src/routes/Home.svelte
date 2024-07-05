<script lang="ts">
    import { onDestroy, onMount } from 'svelte';
    import type { MessageInterface, SessionInterface } from '../common/interfaces';
    import { BACKEND_URI } from '../common/utils';
    import ChatComponent from '../lib/ChatContent.svelte'
    import InputBar from '../lib/Inputbar.svelte';
    import { webSocketStore } from '../common/stores';
    import type { Unsubscriber } from 'svelte/motion';

    export let session_id: string = '';
    export let agent_id: string = '';
    
    let sessions: SessionInterface[] = [];
    let messages: MessageInterface[] = [];
    let agentName: string = 'Assistant';
    let loading: boolean = true;
    let unsubscribe: Unsubscriber | null = null;
    let socket: WebSocket;
    let streaming_response: boolean = false;

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
            webSocketStore.send(JSON.stringify({type: "chat_message", action: "send", content: query}));
            streaming_response = false
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
                webSocketStore.send(JSON.stringify({type: "sessions", action: "get", agent_id: agent_id}));
            } else if (session_id) {
                webSocketStore.send(JSON.stringify({type: "chat_history", action: "get", session_id: session_id}));
            }
        }
    }

    const startWebSocketConnection = ()=>{
        unsubscribe = webSocketStore.subscribe((event: {socket: WebSocket, type: string, data?: any})=>{
            socket = event.socket
            if (event !== null){
                if (event.type === 'open') {
                    if (socket && socket.readyState === WebSocket.OPEN) {
                        webSocketStore.send(JSON.stringify({type: "sessions", action: "list"}));
                        handleRouteChange();
                    }
                }
                else if (event.type === 'message'){
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
                        const new_message = {
                            role: agentName,
                            content: data.content
                        }
    
                        if (!streaming_response){
                            messages = [...messages, new_message]
                        }
                        else {
                            messages = [...messages.slice(0, -1), new_message]
                        }
    
                        streaming_response = true
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
                }
            }
        })
    }

    onMount(() => {
        startWebSocketConnection();

        const unsubscribe = webSocketStore.subscribe((event: {event: string, data?: any})=>{

        })

        return (()=>{
            if (unsubscribe)
                unsubscribe()
        })
    });

    onDestroy(()=>{
        if (unsubscribe)
            unsubscribe();
    })

    $: if (agent_id || session_id) {
        resetState();
        handleRouteChange();
    }
</script>

<ChatComponent {messages} {sessions}>
    <InputBar {uploadFile} {sendMessage} {loading}/>
</ChatComponent>