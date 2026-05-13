import React from 'react';

import { cn } from '../../lib/utils';

export function Tabs({ className, ...props }) {
  return <div className={cn('ui-tabs', className)} {...props} />;
}

export function TabsList({ className, ...props }) {
  return <div className={cn('ui-tabs-list', className)} {...props} />;
}

export function TabsTrigger({ className, active, ...props }) {
  return <button className={cn('ui-tabs-trigger', active && 'is-active', className)} {...props} />;
}
