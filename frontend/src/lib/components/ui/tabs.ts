import { cn } from "$lib/utils";
import type { SvelteHTMLElements } from "svelte/elements";
import { writable } from 'svelte/store';

type TabsProps = {
  defaultValue?: string;
  onValueChange?: (value: string) => void;
};

function createTabs(props: TabsProps = {}) {
  const { defaultValue, onValueChange } = props;
  const value = writable(defaultValue);

  function setValue(newValue: string) {
    value.set(newValue);
    if (onValueChange) {
      onValueChange(newValue);
    }
  }

  return {
    value,
    setValue
  };
}

function tabsList(node: HTMLDivElement, props: SvelteHTMLElements['div']) {
  function updateClass() {
    node.className = cn(
      "inline-flex h-10 items-center justify-center rounded-md bg-muted p-1 text-muted-foreground",
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

function tabsTrigger(node: HTMLButtonElement, props: SvelteHTMLElements['button'] & { value: string, activeValue: string }) {
  function updateClass() {
    const isActive = props.value === props.activeValue;
    node.className = cn(
      "inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1.5 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
      isActive && "bg-background text-foreground shadow-sm",
      props.class
    );
    node.setAttribute('data-state', isActive ? 'active' : 'inactive');
  }
  
  updateClass();

  return {
    update(newProps: SvelteHTMLElements['button'] & { value: string, activeValue: string }) {
      props = newProps;
      updateClass();
    }
  };
}

function tabsContent(node: HTMLDivElement, props: SvelteHTMLElements['div'] & { value: string, activeValue: string }) {
  function updateClass() {
    const isActive = props.value === props.activeValue;
    node.className = cn(
      "mt-2 ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      props.class
    );
    node.setAttribute('data-state', isActive ? 'active' : 'inactive');
    node.hidden = !isActive;
  }
  
  updateClass();

  return {
    update(newProps: SvelteHTMLElements['div'] & { value: string, activeValue: string }) {
      props = newProps;
      updateClass();
    }
  };
}

export { createTabs, tabsList, tabsTrigger, tabsContent };
export type { TabsProps };