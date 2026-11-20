import type { EventType } from '@/api/types';

/** FACTORY_OUTAGE -> "Factory outage". */
export function eventTypeLabel(t: EventType | string): string {
  const s = t.toLowerCase().replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}
