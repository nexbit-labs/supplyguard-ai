import type { EventType } from '@/api/types';

const EVENT_LABELS: Record<EventType, string> = {
  FACTORY_OUTAGE: 'Factory outage',
  SHIPMENT_DELAY: 'Shipment delay',
  QUALITY_HOLD: 'Quality hold',
  CAPACITY_REDUCTION: 'Capacity reduction',
  LOGISTICS_DISRUPTION: 'Logistics disruption',
};

export const eventTypeLabel = (t: EventType): string => EVENT_LABELS[t] ?? t;

/** "just now", "5 min ago", "3 h ago", "2 d ago". Future/invalid timestamps read "just now". */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '-';
  const s = Math.floor(((now || t) - t) / 1000);
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
}

/** hh:mm:ss in 24h local time. */
export function clockTime(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
