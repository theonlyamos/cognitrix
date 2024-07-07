<script lang="ts">
    export let uploadFile: Function;
    export let sendMessage: Function;
    export let loading: boolean = false;

    let inputElement: any;
    let userInput = 'Enter prompt here...';

    const onfocus = (event: FocusEvent)=>{
        if (userInput === 'Enter prompt here...')
            userInput = '';
    }

    function handleKeyDown(event: KeyboardEvent) {
        if (event.key === "Enter") {
            event.preventDefault();
            if (userInput) onSendMessage()
        }
    }

    const onSendMessage = async()=>{
        await sendMessage(userInput)
        userInput = '';
    }


</script>

<div class="input-bar">
    <div class="input-container">
        <button
            on:click={()=>{uploadFile()}}
        >
            <i class="fas fa-paperclip"></i>
        </button>
        <div 
            role="textbox"
            tabindex="0"
            class="input-box" 
            contenteditable="true" 
            on:focus={onfocus}
            on:keydown={handleKeyDown}
            bind:innerText={userInput}
            bind:this={inputElement}
        >{userInput}</div>
        <button 
            on:click={onSendMessage}
            disabled={!userInput || loading}
            class={`${userInput || loading ? '' : 'disabled'}`}
        >
            {#if loading}
            <i class="fas fa-circle-stop"></i>
            {:else}
            <i class="fas fa-paper-plane"></i>
            {/if}
        </button>
    </div>
</div>

<style>
    .input-bar {
        width: 95%;
        height: 11%;
        display: flex;
        justify-content: center;
        align-items: center;
        position: absolute;
        right: 20px;
        backdrop-filter: blur(5px);
    }
    .input-container {
        width: 100%;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-radius: 20px;
        font-size: 0.9rem;
        background-color: var(--bg-1);
        color: var(--fg-2);
        padding: 5px 20px;
        max-height: 63px;
    }

    .input-box {
        text-align: start; 
        outline: none;
        padding: 10px;
        width: 90%;
        max-height: 42px;
        overflow-y: auto;
    }

    button {
        background-color: var(--bg-1);
        padding: 0;
        border: 0;
        outline: none;

        &:hover,&:focus {
            border: none;
            padding: 0 !important;
            outline: none;
        }
    }

    i {
        color: var(--fg-2);
    }
</style>