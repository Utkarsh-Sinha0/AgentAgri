import React from 'react';
import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils';

const badgeVariants = cva('ui-badge', {
  variants: {
    variant: {
      default: 'ui-badge-default',
      success: 'ui-badge-success',
      warning: 'ui-badge-warning',
      destructive: 'ui-badge-destructive',
      outline: 'ui-badge-outline',
    },
  },
  defaultVariants: {
    variant: 'default',
  },
});

export function Badge({ className, variant, ...props }) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
