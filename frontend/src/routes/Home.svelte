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

    const clearMessages = async()=>{
        if (socket && socket.readyState === WebSocket.OPEN) {
            webSocketStore.send(JSON.stringify({type: "chat_history", action: "delete", session_id: session_id}));
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
            if (event !== null){
                socket = event.socket
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
                        if (data.action == 'delete'){
                            messages = []
                            window.location.reload()
                        }
                        else {
                            for (let msg of data.content) {
                                messages = [...messages, {
                                    role: msg.role.toLowerCase(),
                                    content: msg.message
                                }]
                            }
                        }
                    }
                    else if (data.type === 'chat_message') {
                        const new_message = {
                            role: agentName,
                            content: data.content
                        }
                        console.log(data.content, data.complete)
                        if (!streaming_response){
                            messages = [...messages, new_message]
                        }
                        else {
                            if (data.complete){
                                messages[messages.length-1].content = new_message.content
                            }
                            else {
                                messages[messages.length-1].content = messages[messages.length-1].content + new_message.content
                            }
                        }
    
                        streaming_response = true
                    }
                    else if (data.type === 'sessions') {
                        if (data.action === 'list') {
                            sessions = data.content as SessionInterface[];
                        }
                        else if (data.action === 'get') {
                            session_id = data.content?.id
                        }
                    }
                }
            }
        })
    }

    onMount(() => {
        startWebSocketConnection();

        return (()=>{
            if (unsubscribe)
                unsubscribe()
        })
    });

    onDestroy(()=>{
        if (unsubscribe)
            unsubscribe()
    })

    $: if (session_id) {
        resetState();
        handleRouteChange();
    }
</script>

<ChatComponent {messages} {sessions} {clearMessages}>
    {#if session_id}
    <InputBar {uploadFile} {sendMessage} {loading}/>
    {/if}
</ChatComponent>