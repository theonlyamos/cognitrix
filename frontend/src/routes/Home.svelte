<script lang="ts">
    import { onMount } from 'svelte';
    import type { MessageInterface, SessionInterface } from '../common/interfaces';
    import { BACKEND_URI } from '../common/utils';
    import ChatComponent from '../lib/ChatContent.svelte'
    import InputBar from '../lib/Inputbar.svelte';

    export let session_id: String = '';
    
    let sessions: SessionInterface[] = [];
    let messages: MessageInterface[] = [];
    let loading: boolean = true;
    let socket: any;

    const uploadFile = ()=>{
        console.log('Uploading file...')
    }

    const sendMessage = async(query: String)=>{
        loading = true;
        messages = [...messages, {
            role: 'user',
            content: query
        }]
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(`{"type": "chat_message", "content": ${query}}`);
        }
    }

    onMount(() => {
        const websocketUrl = new URL(BACKEND_URI.replace('http', 'ws')).origin;
        socket = new WebSocket(websocketUrl + '/ws');
        
        socket.onopen = () => {
            if (session_id) {
                socket.send(`{"type": "chat_history", "content": "${session_id}"}`);
            }
            socket.send('{"type": "sessions", "content": ""}');
        };

        socket.onmessage = (event: MessageEvent) => {
            loading = false
            let data = JSON.parse(event.data)
            if (data.type === 'chat_history') {
                for (let msg of data.content) {
                    messages = [...messages, {
                        role: msg.role.toLowerCase(),
                        content: msg.message
                    }]
                }
            }
            else if (data.type === 'chat_reply') {
                messages = [...messages, {
                    role: 'assistant',
                    content: data.content
                }]
            }
            else if (data.type === 'sessions') {
                sessions = data.content as SessionInterface[];
            }
        };

        return (()=>{
            socket.close()
        })
    });

</script>

<ChatComponent {messages} {sessions}>
    <InputBar {uploadFile} {sendMessage} {loading}/>
</ChatComponent>