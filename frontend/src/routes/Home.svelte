<script lang="ts">
  import { onMount } from 'svelte';
    import type { MessageInterface } from '../common/interfaces';
    import { BACKEND_URI } from '../common/utils';
    import ChatComponent from '../lib/ChatContent.svelte'
    import InputBar from '../lib/Inputbar.svelte';

    let messages: MessageInterface[] = [];
    let loading: Boolean = false;
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
            socket.send(`{"type": "chat", "content": ${query}}`);
        }
    }

    onMount(() => {
        const websocketUrl = new URL(BACKEND_URI.replace('http', 'ws')).origin;
        socket = new WebSocket(websocketUrl + '/ws');
        
        socket.onopen = () => {
            socket.send('{"type": "session", "content": ""}');
        };

        socket.onmessage = (event: MessageEvent) => {
            loading = false
            let data = JSON.parse(event.data)
            if (data.type === 'session') {
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
        };

        return (()=>{
            socket.close()
        })
    });

</script>

<ChatComponent {messages}/>
<InputBar {uploadFile} {sendMessage} {loading}/>