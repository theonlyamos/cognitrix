import { cn } from "$lib/utils";
import { cva, type VariantProps } from "class-variance-authority";
import type { SvelteHTMLElements } from "svelte/elements";

const labelVariants = cva(
  "text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
);

type LabelProps = SvelteHTMLElements['label'] & VariantProps<typeof labelVariants>;

function label(node: HTMLLabelElement, props: LabelProps) {
  function updateClass() {
    node.className = cn(labelVariants(), props.class);
  }
  
  updateClass();

  return {
    update(newProps: LabelProps) {
      props = newProps;
      updateClass();
    }
  };
}

export { label, labelVariants };
export type { LabelProps };