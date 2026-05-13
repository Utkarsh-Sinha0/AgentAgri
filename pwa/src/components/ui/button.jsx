import React from 'react';
import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils';

const buttonVariants = cva('ui-button', {
  variants: {
    variant: {
      default: 'ui-button-default',
      secondary: 'ui-button-secondary',
      destructive: 'ui-button-destructive',
      ghost: 'ui-button-ghost',
    },
    size: {
      default: 'ui-button-md',
      sm: 'ui-button-sm',
      icon: 'ui-button-icon',
    },
  },
  defaultVariants: {
    variant: 'default',
    size: 'default',
  },
});

export function Button({ className, variant, size, ...props }) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
