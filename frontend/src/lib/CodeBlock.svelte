<script lang="ts">
  import { run } from 'svelte/legacy';

  import { onMount } from "svelte";

  interface Props {
    htmlContent?: string | Promise<string>;
  }

  let { htmlContent = "" }: Props = $props();

  let formattedContent: string = $state("");

  onMount(async () => {
    await highlightCodeBlocks();
  });

  async function highlightCodeBlocks() {
    const resolvedContent = await Promise.resolve(htmlContent);
    const parser = new DOMParser();
    const doc = parser.parseFromString(resolvedContent, "text/html");

    doc.querySelectorAll("pre code").forEach((block) => {
      if (block instanceof HTMLElement) {
        block.classList.add("highlighted-code");
        const language = block.className.split("-")[1] || "plaintext";
        const preElement = block.parentElement;
        if (preElement instanceof HTMLPreElement) {
          preElement.setAttribute("data-language", language.split(" ")[0]);
        }
      }
    });

    formattedContent = doc.body.innerHTML;
  }

  run(() => {
    if (htmlContent) {
      highlightCodeBlocks();
    }
  });
</script>

<div class="content-container">
  {@html formattedContent}
</div>

<style>
  .content-container {
    font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
    line-height: 1.6;
    color: var(--fg-1);
    inline-size: 100%;
  }

  :global(.content-container h1),
  :global(.content-container h2),
  :global(.content-container h3),
  :global(.content-container h4),
  :global(.content-container h5),
  :global(.content-container h6) {
    margin-bottom: 0.5em;
  }

  :global(.content-container h1) {
    font-size: 1.5rem;
  }

  :global(.content-container h2) {
    font-size: 1.3rem;
  }

  :global(.content-container p) {
    margin-bottom: 1em;
  }

  :global(.content-container pre) {
    background-color: #282c34;
    border-radius: 8px;
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    /* margin: 1.5em 0; */
    position: relative;
    padding: 1em;
  }

  :global(.content-container .highlighted-code) {
    display: block;
    font-family: "Fira Code", "Consolas", "Monaco", monospace;
    font-size: 0.9em;
    line-height: 1.5;
    color: #abb2bf;
    overflow-x: auto;
  }

  :global(.content-container pre::before) {
    content: attr(data-language);
    position: absolute;
    top: 0;
    right: 0;
    padding: 0.5em 1em;
    font-size: 0.75em;
    background-color: rgba(255, 255, 255, 0.1);
    color: #abb2bf;
    border-bottom-left-radius: 8px;
    border-top-right-radius: 8px;
    text-transform: uppercase;
  }

  :global(.content-container a) {
    color: #3498db;
    text-decoration: none;
    transition: color 0.3s ease;
  }

  :global(.content-container a:hover) {
    color: #2980b9;
    text-decoration: underline;
  }

  :global(.content-container p:last-of-type) {
    margin-bottom: 0;
  }

  :global(.content-container ul),
  :global(.content-container ol) {
    padding-left: 2em;
    margin-bottom: 1em;
  }

  :global(.content-container blockquote) {
    border-left: 4px solid #3498db;
    padding-left: 1em;
    margin: 1em 0;
    font-style: italic;
    color: #7f8c8d;
  }
</style>
