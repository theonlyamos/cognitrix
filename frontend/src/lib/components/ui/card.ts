import { cn } from "$lib/utils";
import type { SvelteHTMLElements } from "svelte/elements";

function card(node: HTMLDivElement, props: SvelteHTMLElements['div']) {
  function updateClass() {
    node.className = cn(
      "rounded-lg border bg-card text-card-foreground shadow-sm",
      props.class
    );
  }
  
  updateClass();

  return {
    update(newProps: SvelteHTMLElements['div']) {
      props = newProps;
      updateClass();
    }
  };
}

function cardHeader(node: HTMLDivElement, props: SvelteHTMLElements['div']) {
  function updateClass() {
    node.className = cn("flex flex-col space-y-1.5 p-6", props.class);
  }
  
  updateClass();

  return {
    update(newProps: SvelteHTMLElements['div']) {
      props = newProps;
      updateClass();
    }
  };
}

function cardTitle(node: HTMLHeadingElement, props: SvelteHTMLElements['h3']) {
  function updateClass() {
    node.className = cn(
      "text-2xl font-semibold leading-none tracking-tight",
      props.class
    );
  }
  
  updateClass();

  return {
    update(newProps: SvelteHTMLElements['h3']) {
      props = newProps;
      updateClass();
    }
  };
}

function cardDescription(node: HTMLParagraphElement, props: SvelteHTMLElements['p']) {
  function updateClass() {
    node.className = cn("text-sm text-muted-foreground", props.class);
  }
  
  updateClass();

  return {
    update(newProps: SvelteHTMLElements['p']) {
      props = newProps;
      updateClass();
    }
  };
}

function cardContent(node: HTMLDivElement, props: SvelteHTMLElements['div']) {
  function updateClass() {
    node.className = cn("p-6 pt-0", props.class);
  }
  
  updateClass();

  return {
    update(newProps: SvelteHTMLElements['div']) {
      props = newProps;
      updateClass();
    }
  };
}

function cardFooter(node: HTMLDivElement, props: SvelteHTMLElements['div']) {
  function updateClass() {
    node.className = cn("flex items-center p-6 pt-0", props.class);
  }
  
  updateClass();

  return {
    update(newProps: SvelteHTMLElements['div']) {
      props = newProps;
      updateClass();
    }
  };
}

export { card, cardHeader, cardFooter, cardTitle, cardDescription, cardContent };