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
        try {
            messages = [...messages, {
                role: 'user',
                content: query
            }]
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(query);
            }
            
            loading = false;
        } catch (error) {
            loading = false;
            console.log(error)
        }
    }

    onMount(() => {
        const websocketUrl = new URL(BACKEND_URI.replace('http', 'ws')).origin;
        socket = new WebSocket(websocketUrl + '/ws');

        socket.onmessage = (event: MessageEvent) => {
            messages = [...messages, {
                role: 'assistant',
                content: event.data
            }]
        };

        return (()=>{
            socket.close()
        })
    });

</script>

<ChatComponent {messages}/>
<InputBar {uploadFile} {sendMessage} {loading}/>