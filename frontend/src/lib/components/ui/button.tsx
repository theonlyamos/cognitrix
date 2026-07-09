import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded font-medium transition-colors focus-visible:outline-none disabled:opacity-50 disabled:pointer-events-none select-none',
  {
    variants: {
      variant: {
        primary: 'bg-accent text-accent-foreground hover:brightness-[1.06]',
        ghost: 'text-fg-dim hover:bg-panel-2 hover:text-fg',
        outline: 'border border-line text-fg hover:bg-panel-2 hover:border-fg-dim',
        danger: 'bg-danger text-white hover:brightness-[1.06]',
        subtle: 'bg-panel-2 text-fg hover:brightness-[1.08]',
      },
      size: {
        sm: 'h-11 px-3 text-[13px] md:h-8',
        md: 'h-11 px-4 text-sm',
        lg: 'h-12 px-6 text-sm',
        icon: 'h-11 w-11 md:h-9 md:w-9',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
