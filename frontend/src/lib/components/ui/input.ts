import { cn } from "$lib/utils";
import type { SvelteHTMLElements } from "svelte/elements";

type InputProps = SvelteHTMLElements['input'];

function input(node: HTMLInputElement, props: InputProps) {
  function updateClass() {
    node.className = cn(
      "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
      props.class
    );
  }
  
  updateClass();

  return {
    update(newProps: InputProps) {
      props = newProps;
      updateClass();
    }
  };
}

export { input };
export type { InputProps };