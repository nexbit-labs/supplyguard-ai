/** "2026-11-19T08:05:00Z" -> "19 Nov 2026, 08:05 UTC". Invalid input is returned as-is. */
export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return (
    new Intl.DateTimeFormat('en-GB', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone: 'UTC',
    }).format(d) + ' UTC'
  );
}

/** "2026-11-30" -> "30 Nov" (UTC, no timezone drift). */
export function shortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
}

/** Compact INR for axis ticks: 4500000 -> ₹45L, 25000000 -> ₹2.5Cr. */
export function compactInr(n: number): string {
  const abs = Math.abs(n);
  const trim = (x: number) => String(Number(x.toFixed(1)));
  if (abs >= 1e7) return `₹${trim(n / 1e7)}Cr`;
  if (abs >= 1e5) return `₹${trim(n / 1e5)}L`;
  if (abs >= 1e3) return `₹${trim(n / 1e3)}K`;
  return `₹${trim(n)}`;
}
